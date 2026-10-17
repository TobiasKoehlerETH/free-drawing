"""Modelled screw threads: detection, ISO designation and drawing simplification.

STEP files from CAD systems often carry *real* helical thread geometry
(swept flanks as B-spline surfaces). Drawn as is, TechDraw projects hundreds of
helix edges and the dimensioning sees no plain diameter. ISO 6410 instead
draws a thread conventionally:

* external thread: outline at the MAJOR diameter, thin continuous line at the
  minor diameter, thin 3/4 circle at the minor diameter in the end view;
* internal thread: hole at the MINOR (tap drill) diameter, thin line at the
  major diameter, thin 3/4 circle at the major diameter in the end view;
* the thread is specified by a callout ``M20×2.5-6g`` / ``2× M8×1.25-6H ↧12``.

This module finds the threads (:func:`detect_threads`), names them
(:func:`iso_designation`), and replaces the helical geometry by the plain
conventional cylinders (:func:`simplify`) so the rest of the pipeline treats
them like any other turned diameter or hole. :func:`tap_drill_matches` flags
plain holes whose size is a standard tap drill (thread possibly not modelled).
"""

import math

import FreeCAD as App
import Part

from .analysis import cylinders

ANALYTIC = ("Plane", "Cylinder", "Cone", "Sphere", "Toroid")
AXES = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}
# right-handed frames (u, v, axis) with u × v = axis
FRAMES = {"X": (1, 2), "Y": (2, 0), "Z": (0, 1)}

# ISO 261 nominal sizes with the coarse pitch, and the common fine pitches
COARSE = {
    1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75,
    14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 33: 3.5, 36: 4.0, 39: 4.0,
    42: 4.5, 45: 4.5, 48: 5.0, 52: 5.0, 56: 5.5, 60: 5.5, 64: 6.0,
}
FINE = {
    2: (0.25,), 2.5: (0.35,), 3: (0.35,), 3.5: (0.35,), 4: (0.5,), 5: (0.5,), 6: (0.75,), 8: (1.0, 0.75),
    10: (1.25, 1.0, 0.75), 12: (1.5, 1.25, 1.0), 14: (1.5, 1.25, 1.0), 16: (1.5, 1.0), 18: (2.0, 1.5, 1.0),
    20: (2.0, 1.5, 1.0), 22: (2.0, 1.5, 1.0), 24: (2.0, 1.5, 1.0), 27: (2.0, 1.5, 1.0), 30: (3.0, 2.0, 1.5, 1.0),
    33: (3.0, 2.0, 1.5), 36: (3.0, 2.0, 1.5), 39: (3.0, 2.0, 1.5), 42: (4.0, 3.0, 2.0, 1.5),
    45: (4.0, 3.0, 2.0, 1.5), 48: (4.0, 3.0, 2.0, 1.5), 52: (4.0, 3.0, 2.0, 1.5), 56: (4.0, 3.0, 2.0, 1.5),
    60: (4.0, 3.0, 2.0, 1.5), 64: (4.0, 3.0, 2.0, 1.5),
}
# ISO 2306 tap drills for coarse threads
TAP_DRILL = {
    1.6: 1.25, 2: 1.6, 2.5: 2.05, 3: 2.5, 3.5: 2.9, 4: 3.3, 5: 4.2, 6: 5.0, 8: 6.8, 10: 8.5, 12: 10.2,
    14: 12.0, 16: 14.0, 18: 15.5, 20: 17.5, 22: 19.5, 24: 21.0, 27: 24.0, 30: 26.5, 33: 29.5, 36: 32.0,
    39: 35.0, 42: 37.5, 45: 40.5, 48: 43.0, 52: 47.0, 56: 50.5, 60: 54.5, 64: 58.0,
}


def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _c(p, i):
    return (p.x, p.y, p.z)[i]


# ------------------------------------------------------------ designation
def nominal_size(major, pitch=None, internal=None):
    """Nearest ISO 261 nominal diameter (None if nothing is within 8 %)."""
    best = min(COARSE, key=lambda d: abs(d - major))
    return best if abs(best - major) <= 0.08 * best + 0.05 else None


def snap_pitch(nominal, pitch):
    """ISO pitch for ``nominal`` closest to ``pitch`` (coarse first), else None."""
    if nominal is None:
        return None
    for p in (COARSE[nominal],) + FINE.get(nominal, ()):
        if abs(p - pitch) <= 0.04 * p + 0.01:
            return p
    return None


def iso_designation(major, pitch):
    """``M20×2.5`` - major snapped to ISO 261 sizes, pitch to the coarse/fine series."""
    nom = nominal_size(major)
    if nom is None:
        return f"M{_fmt(major)}×{_fmt(pitch)}"
    p = snap_pitch(nom, pitch) or pitch
    return f"M{_fmt(nom)}×{_fmt(p)}"


def callout(t, count=1):
    """Thread callout text: ``2× M8×1.25-6H ↧12``, ``M20×2.5-6g``, ``... LH``."""
    txt = (f"{count}× " if count > 1 else "") + t["designation"] + "-" + ("6H" if t["internal"] else "6g")
    if t["handed"] == "LH":
        txt += " LH"
    if t["internal"]:
        txt += " THRU" if t["through"] else f" ↧{_fmt(t['length'])}"
    return txt


def tap_drill_matches(dia, tol=0.02):
    """Coarse threads whose ISO tap drill is ``dia``: ``[{"thread": "M8×1.25", "tap_drill": 6.8}]``."""
    out = []
    for nom, td in TAP_DRILL.items():
        if abs(td - dia) <= tol:
            out.append({"thread": f"M{_fmt(nom)}×{_fmt(COARSE[nom])}", "tap_drill": td})
    return out


# ------------------------------------------------------------- detection
def _edge_points(edge, r_guess, per_turn=24):
    """Polyline of an edge, dense enough to unwrap a helix (< 15 deg per step)."""
    try:
        turns = edge.Length / (2 * math.pi * max(r_guess, 0.1))
    except Exception:
        turns = 1.0
    n = int(min(3000, max(12, per_turn * turns)))
    try:
        return edge.discretize(n)
    except Exception:
        return [v.Point for v in edge.Vertexes]


def _local(p, axis, line):
    """(radius, angle, axial) of ``p`` about the line through ``line`` along ``axis``."""
    i = "XYZ".index(axis)
    ju, jv = FRAMES[axis]
    du, dv = _c(p, ju) - line[ju], _c(p, jv) - line[jv]
    return math.hypot(du, dv), math.atan2(dv, du), _c(p, i)


def _candidate_lines(cyls, faces):
    """Possible thread axes: cylinder axes, plus axes of the faces' own bounding boxes."""
    lines = []
    for c in cyls:
        if c["axis"]:
            lines.append((c["axis"], tuple(c["center"])))
    for f in faces:
        bb = f.BoundBox
        ext = {"X": bb.XLength, "Y": bb.YLength, "Z": bb.ZLength}
        for a in "XYZ":
            o = [k for k in "XYZ" if k != a]
            if ext[o[0]] > 1e-6 and abs(ext[o[0]] - ext[o[1]]) <= 0.05 * max(ext[o[0]], ext[o[1]]):
                lines.append((a, (bb.Center.x, bb.Center.y, bb.Center.z)))
    out = []
    for a, c in lines:
        i = "XYZ".index(a)
        if not any(b == a and all(abs(c[k] - d[k]) < 1e-3 for k in range(3) if k != i) for b, d in out):
            out.append((a, c))
    return out


def _fit_face(edge_pts, lines):
    """Best axis line for a thread face: smallest radial spread, wrapping the axis."""
    pts = [p for run in edge_pts for p in run]
    if len(pts) < 8:
        return None
    sub = pts[:: max(1, len(pts) // 400)]
    best = None
    for axis, line in lines:
        loc = [_local(p, axis, line) for p in sub]
        rs = [l[0] for l in loc]
        rmin, rmax = min(rs), max(rs)
        if rmax < 1e-6 or rmax - rmin > 0.5 * rmax:
            continue
        # the face must go round the axis, not sit beside it
        bins = {int(((l[1] + math.pi) / (2 * math.pi)) * 12) % 12 for l in loc}
        if len(bins) < 9:
            continue
        spread = (rmax - rmin) / rmax
        if best is None or spread < best[0]:
            best = (spread, axis, line)
    return best


def _helix_slope(loc_runs):
    """Weighted slope d(axial)/d(angle) over helical runs; None if none."""
    num = den = 0.0
    for loc in loc_runs:
        if len(loc) < 6:
            continue
        rs = [l[0] for l in loc]
        if max(rs) - min(rs) > 0.03 * max(rs) + 0.01:
            continue  # not a constant-radius helix (end curve, flank cut)
        th = [loc[0][1]]
        for prev, l in zip(loc, loc[1:]):
            d = (l[1] - prev[1] + math.pi) % (2 * math.pi) - math.pi  # unwrap
            th.append(th[-1] + d)
        span = th[-1] - th[0]
        if abs(span) < 0.5 * math.pi:
            continue
        z = [l[2] for l in loc]
        n = len(th)
        mt, mz = sum(th) / n, sum(z) / n
        stt = sum((t - mt) ** 2 for t in th)
        if stt < 1e-12:
            continue
        s = sum((t - mt) * (zz - mz) for t, zz in zip(th, z)) / stt
        w = abs(span)
        num += s * w
        den += w
    return num / den if den else None


def _inside(shape, p):
    try:
        return shape.isInside(App.Vector(*p), 1e-4, True)
    except Exception:
        return False


def _point(axis, line, r, ang, t):
    i = "XYZ".index(axis)
    ju, jv = FRAMES[axis]
    p = list(line)
    p[i] = t
    p[ju] += r * math.cos(ang)
    p[jv] += r * math.sin(ang)
    return tuple(p)


def _coaxial_cyl(cyls, axis, line, r, tol=0.03):
    i = "XYZ".index(axis)
    best = None
    for c in cyls:
        if c["axis"] != axis or any(abs(c["center"][k] - line[k]) > 1e-3 for k in range(3) if k != i):
            continue
        if abs(c["radius"] - r) <= tol and (best is None or abs(c["radius"] - r) < abs(best["radius"] - r)):
            best = c
    return best


def _material_beyond(shape, planes, axis, line, r, t, sign):
    """Is there material just beyond axial position ``t`` (towards ``sign``) at radius ``r``?

    Decided from planar end faces at ``t`` (their outward normal tells which
    side holds material); a point classification is the fallback.
    """
    probes = [App.Vector(*_point(axis, line, r, a, t)) for a in (0.3, 1.9, 3.5, 5.1)]
    i = "XYZ".index(axis)
    vote = 0
    for f, n_ax in planes:
        if abs(_c(f.Surface.Position, i) - t) > 2e-3:
            continue
        bb = f.BoundBox
        for p in probes:
            if not bb.isInside(p):
                continue
            try:
                if f.distToShape(Part.Vertex(p))[0] > 1e-4:
                    continue
            except Exception:
                continue
            n = n_ax * (1 if f.Orientation == "Forward" else -1)
            vote += -1 if n * sign > 0 else 1  # normal pointing beyond: material on our side only
            break
    if vote:
        return vote > 0
    return any(_inside(shape, _point(axis, line, r, a, t + sign * 0.3)) for a in (0.3, 3.5))


def detect_threads(shape):
    """Modelled helical threads of ``shape`` (aligned to X/Y/Z).

    Returns ``[{"axis", "center", "internal", "major_dia", "minor_dia", "pitch",
    "handed", "extent", "through", "faces", "designation", "nominal", "length",
    "open": (lo_open, hi_open)}]``.
    """
    cand = [(k, f) for k, f in enumerate(shape.Faces) if type(f.Surface).__name__ not in ANALYTIC]
    if not cand:
        return []
    cyls = cylinders(shape)
    lines = _candidate_lines(cyls, [f for _, f in cand])
    fitted = []
    for k, f in cand:
        edge_pts = []
        for e in f.Edges:
            bb = e.BoundBox
            edge_pts.append(_edge_points(e, 0.5 * max(bb.XLength, bb.YLength, bb.ZLength, 0.1)))
        fit = _fit_face(edge_pts, lines)
        if fit is None:
            continue
        _, axis, line = fit
        runs = [[_local(p, axis, line) for p in pts] for pts in edge_pts]
        allp = [l for run in runs for l in run]
        fitted.append({"face": k, "axis": axis, "line": line, "runs": runs,
                       "rmin": min(l[0] for l in allp), "rmax": max(l[0] for l in allp),
                       "lo": min(l[2] for l in allp), "hi": max(l[2] for l in allp)})
    # group coaxial faces, then split along the axis where they do not overlap
    groups = {}
    for fi in fitted:
        i = "XYZ".index(fi["axis"])
        key = (fi["axis"],) + tuple(round(fi["line"][j], 1) for j in range(3) if j != i)
        groups.setdefault(key, []).append(fi)
    planes = {}
    for f in shape.Faces:
        if type(f.Surface).__name__ == "Plane":
            n = App.Vector(f.Surface.Axis)
            for a in "XYZ":
                comp = _c(n, "XYZ".index(a))
                if abs(abs(comp) - 1) < 1e-6:
                    planes.setdefault(a, []).append((f, 1 if comp > 0 else -1))
    out = []
    for key, items in groups.items():
        items.sort(key=lambda x: x["lo"])
        clusters, top = [], None
        for it in items:
            if clusters and it["lo"] <= top + 0.5:
                clusters[-1].append(it)
                top = max(top, it["hi"])
            else:
                clusters.append([it])
                top = it["hi"]
        for cl in clusters:
            axis, line = cl[0]["axis"], cl[0]["line"]
            slope = _helix_slope([run for it in cl for run in it["runs"]])
            if slope is None or abs(slope) < 1e-6:
                continue  # free-form but not helical (blend, spline boss)
            pitch = abs(slope) * 2 * math.pi
            rmin = min(it["rmin"] for it in cl)
            rmax = max(it["rmax"] for it in cl)
            depth = rmax - rmin
            if depth < 0.3 * pitch or depth > 1.2 * pitch:
                continue  # not a thread profile
            lo = round(min(it["lo"] for it in cl), 3) + 0.0
            hi = round(max(it["hi"] for it in cl), 3) + 0.0
            # root/crest cylinder between the turns: exact minor radius and the side of the material
            root = _coaxial_cyl(cyls, axis, line, rmin)
            if root is not None:
                rmin = root["radius"]
                internal = root["internal"]
            else:
                dr = min(0.3, 0.1 * rmin)
                votes = sum(_inside(shape, _point(axis, line, rmin - dr, a, 0.5 * (lo + hi))) for a in (0.3, 1.9, 3.5))
                internal = votes < 2
            # an end is "open" when no material lies beyond it inside the thread annulus
            rt = 0.5 * (rmin + rmax)
            pl = planes.get(axis, [])
            open_lo = not _material_beyond(shape, pl, axis, line, rt, lo, -1)
            open_hi = not _material_beyond(shape, pl, axis, line, rt, hi, +1)
            through = bool(internal and open_lo and open_hi)
            major, minor = 2 * rmax, 2 * rmin
            nom = nominal_size(major)
            p_iso = snap_pitch(nom, pitch) if nom else None
            i = "XYZ".index(axis)
            center = [round(v, 4) + 0.0 for v in line]
            center[i] = lo
            out.append({
                "axis": axis, "center": tuple(center), "internal": bool(internal),
                "major_dia": round(major, 4), "minor_dia": round(minor, 4), "pitch": round(p_iso or pitch, 4),
                "measured_pitch": round(pitch, 4), "handed": "RH" if slope > 0 else "LH",
                "extent": (lo, hi), "length": round(hi - lo, 3), "through": through,
                "open": (bool(open_lo), bool(open_hi)), "faces": sorted(it["face"] for it in cl),
                "nominal": nom, "designation": iso_designation(major, p_iso or pitch),
            })
    out.sort(key=lambda t: (t["axis"], t["center"]))
    return out


# ------------------------------------------------------------ simplify
def _cyl(axis, line, r, lo, hi):
    i = "XYZ".index(axis)
    base = list(line)
    base[i] = lo
    return Part.makeCylinder(r, hi - lo, App.Vector(*base), AXES[axis])


def _tools(t):
    """(add, remove) solids that turn thread ``t`` into its ISO 6410 drawing geometry."""
    axis, line = t["axis"], t["center"]
    lo, hi = t["extent"]
    if t["internal"]:
        # fill the thread space, re-drill at the minor (tap drill) diameter
        fill = _cyl(axis, line, t["major_dia"] / 2.0 + 0.1, lo, hi)
        ext_lo = lo - (0.5 if t["open"][0] else 0.0)
        ext_hi = hi + (0.5 if t["open"][1] else 0.0)
        return [fill], [_cyl(axis, line, t["minor_dia"] / 2.0, ext_lo, ext_hi)]
    # cut the thread zone, put back a plain cylinder at the nominal major diameter
    r_out = (t["nominal"] or t["major_dia"]) / 2.0
    if r_out < t["major_dia"] / 2.0 - 0.05:
        r_out = t["major_dia"] / 2.0
    return [_cyl(axis, line, r_out, lo, hi)], [_cyl(axis, line, t["major_dia"] / 2.0 + 0.1, lo, hi)]


def _apply(shape, threads, n_solids):
    ext = [t for t in threads if not t["internal"]]
    inn = [t for t in threads if t["internal"]]
    s = shape
    if ext:
        adds, cuts = zip(*[_tools(t) for t in ext])
        s = s.cut([c for cs in cuts for c in cs]).fuse([a for ad in adds for a in ad])
    if inn:
        adds, cuts = zip(*[_tools(t) for t in inn])
        s = s.fuse([a for ad in adds for a in ad]).cut([c for cs in cuts for c in cs])
    s = s.removeSplitter()
    if len(s.Solids) != n_solids or not s.isValid():
        s.fix(1e-7, 1e-7, 1e-7)
    if len(s.Solids) != n_solids or not s.isValid():
        raise ValueError("boolean result is not a valid solid")
    if any(type(f.Surface).__name__ not in ANALYTIC and _near_threads(f, threads) for f in s.Faces):
        raise ValueError("helical faces remain")
    return s


def _near_threads(face, threads):
    bb = face.BoundBox
    for t in threads:
        i = "XYZ".index(t["axis"])
        lo, hi = t["extent"]
        if getattr(bb, t["axis"] + "Max") >= lo - 1e-3 and getattr(bb, t["axis"] + "Min") <= hi + 1e-3:
            c = t["center"]
            r = t["major_dia"] / 2.0 + 0.2
            if all(getattr(bb, "XYZ"[k] + "Min") >= c[k] - r and getattr(bb, "XYZ"[k] + "Max") <= c[k] + r
                   for k in range(3) if k != i):
                return True
    return False


def simplify(shape, threads):
    """ISO 6410 drawing geometry: threads replaced by plain cylinders.

    External: the thread zone is cut away and a cylinder at the nominal MAJOR
    diameter put back; internal: the thread zone is filled and re-drilled at
    the MINOR (tap drill) diameter. All threads go in one boolean pass; if that
    fails, thread by thread. Each thread dict gets ``simplified`` (bool) and,
    on failure, ``simplify_error``; a thread that fails stays as modelled.
    Returns a valid shape (the input itself when nothing could be simplified).
    """
    if not threads:
        return shape
    n_solids = len(shape.Solids)
    try:
        s = _apply(shape, threads, n_solids)
        for t in threads:
            t["simplified"] = True
    except Exception:
        s = shape
        for t in threads:
            try:
                s = _apply(s, [t], n_solids)
                t["simplified"] = True
            except Exception as e:
                t["simplified"] = False
                t["simplify_error"] = str(e)
    if s is not shape and len(s.Solids) == 1:
        s = s.Solids[0]
    return s


def summary(t):
    """JSON-friendly record for page metadata."""
    keys = ("axis", "center", "internal", "major_dia", "minor_dia", "pitch", "handed", "extent", "length",
            "through", "designation", "nominal")
    out = {k: (list(t[k]) if isinstance(t[k], tuple) else t[k]) for k in keys}
    out["callout"] = callout(t)
    return out
