"""3D feature recognition -> dimensioning requirements.

A *requirement* is one controlling piece of information the drawing must
state exactly once [04 "state each essential size once"]:

``loc``      position of a feature along a model axis, measured from that
             axis' datum (planar faces, hole/boss centres, edges of inclined faces)
``dia``      diameter callout of a hole stack / boss / shaft step
``radius``   radius callout of fillets/rounds, grouped (``4× R5``)
``chamfer``  45° chamfers, grouped (``2× C1.5``)
``angle``    angle of an inclined (non-chamfer) face
``pcd``      pitch circle of an equally spaced hole pattern (``⌀90``, ``6× ... EQ SP``)
``sphere``   ``SR`` / ``SØ``

Everything is computed on the aligned 3D shape, independent of views, so the
same rules hold for any STEP input.
"""

import math
from collections import defaultdict

import FreeCAD as App

from . import threads as threads_mod
from .analysis import cylinders
from .geometry import exact_bbox

AX = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}
TOL = 1e-3


def _axis_name(v, tol=1e-4):
    v = App.Vector(v)
    if v.Length < 1e-12:
        return None
    v.normalize()
    for k, a in AX.items():
        if abs(abs(v.dot(a)) - 1) < tol:
            return k
    return None


def _coord(p, axis):
    return {"X": p[0], "Y": p[1], "Z": p[2]}[axis]


def _r(v, n=3):
    return round(float(v), n)


def _inside(shape, p):
    try:
        return shape.isInside(App.Vector(*p), 1e-4, True)
    except Exception:
        return False


# ---------------------------------------------------------------- holes
def hole_stacks(shape, cyls):
    """Coaxial internal cylinders/cones grouped into hole stacks.

    Returns ``[{"axis", "center", "dia", "through", "depth", "cbore": (dia, depth)|None,
    "csk": (dia, angle)|None}]`` – one per physical hole.
    """
    bb = exact_bbox(shape)
    lines = defaultdict(list)
    for c in cyls:
        if not c["internal"] or not c["axis"]:
            continue
        i = "XYZ".index(c["axis"])
        key = (c["axis"],) + tuple(_r(c["center"][k], 2) for k in range(3) if k != i)
        lines[key].append(c)
    cones = []
    for f in shape.Faces:
        if type(f.Surface).__name__ == "Cone":
            ax = _axis_name(f.Surface.Axis)
            if ax:
                cones.append((ax, f))
    out = []
    for key, segs in lines.items():
        axis = key[0]
        i = "XYZ".index(axis)
        segs.sort(key=lambda c: c["radius"])
        main = segs[0]
        if not main["full"]:
            continue
        lo = min(s["extent"][0] for s in segs)
        hi = max(s["extent"][1] for s in segs)
        c = list(main["center"])
        eps = 0.05
        below = list(c); below[i] = lo - eps
        above = list(c); above[i] = hi + eps
        through = not _inside(shape, below) and not _inside(shape, above)
        cbore = None
        for s in segs[1:]:
            if s["full"] and s["radius"] > main["radius"] + TOL:
                cbore = (2 * s["radius"], s["extent"][1] - s["extent"][0])
                break
        csk = None
        for ax, f in cones:
            if ax != axis:
                continue
            apex = f.Surface.Apex
            on_line = all(abs(_coord((apex.x, apex.y, apex.z), "XYZ"[k]) - c[k]) < 1e-2 for k in range(3) if k != i)
            if on_line:
                j, k = [x for x in range(3) if x != i]
                fb = f.BoundBox
                spans = (fb.XLength, fb.YLength, fb.ZLength)
                d_big = max(spans[j], spans[k])
                csk = (d_big, abs(2 * math.degrees(f.Surface.SemiAngle)))
                break
        depth = main["extent"][1] - main["extent"][0]
        out.append({"axis": axis, "center": tuple(c), "dia": 2 * main["radius"], "through": through,
                    "depth": depth, "cbore": cbore, "csk": csk, "extent": (lo, hi)})
    return out


def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def hole_text(h, n):
    t = (f"{n}× " if n > 1 else "") + "⌀%.2w"
    t += " THRU" if h["through"] else f" ↧{_fmt(h['depth'])}"
    if h["cbore"]:
        t += f" ⌴⌀{_fmt(h['cbore'][0])} ↧{_fmt(h['cbore'][1])}"
    if h["csk"]:
        t += f" ⌵⌀{_fmt(h['csk'][0])}×{_fmt(h['csk'][1])}°"
    return t


def bolt_circles(groups):
    """Equally spaced holes on a circle -> PCD patterns (>= 3 holes)."""
    out = []
    for g in groups:
        items = g["items"]
        if len(items) < 3:
            continue
        i = "XYZ".index(g["axis"])
        j, k = [x for x in range(3) if x != i]
        pts = [(h["center"][j], h["center"][k]) for h in items]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        rs = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
        if min(rs) < 1e-3 or max(rs) - min(rs) > 1e-2:
            continue
        angs = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in pts)
        steps = [(angs[(n + 1) % len(angs)] - angs[n]) % (2 * math.pi) for n in range(len(angs))]
        if max(steps) - min(steps) > 1e-2:
            continue
        # rectangular 2x2 patterns are better dimensioned by coordinates
        if len(pts) == 4 and all(abs(abs(math.cos(a)) - abs(math.sin(a))) < 1e-2 for a in angs):
            continue
        center = [0.0, 0.0, 0.0]
        center[i] = items[0]["center"][i]
        center[j], center[k] = cx, cy
        start = math.degrees(angs[0]) % (360.0 / len(pts))
        out.append({"group": g, "center": tuple(center), "pcd": 2 * rs[0], "axis": g["axis"], "start_angle": start})
    return out


# -------------------------------------------------------------- faces
def plane_positions(shape):
    """Positions of planar faces normal to each axis: ``{axis: {coord: area}}``."""
    pos = {k: defaultdict(float) for k in "XYZ"}
    inclined = []
    for f in shape.Faces:
        if type(f.Surface).__name__ != "Plane":
            continue
        n = App.Vector(f.Surface.Axis)
        ax = _axis_name(n)
        if ax:
            c = _coord((f.Surface.Position.x, f.Surface.Position.y, f.Surface.Position.z), ax)
            pos[ax][_r(c)] += f.Area
        else:
            inclined.append(f)
    return pos, inclined


def classify_inclined(face, shape):
    """'chamfer' (45°, narrow) or 'incline'."""
    n = App.Vector(face.Surface.Axis)
    n.normalize()
    comps = sorted([abs(n.x), abs(n.y), abs(n.z)], reverse=True)
    is45 = comps[2] < 1e-4 and abs(comps[0] - comps[1]) < 1e-3
    lines = [e for e in face.Edges if type(e.Curve).__name__ == "Line"]
    width = None
    if lines:
        longest = max(lines, key=lambda e: e.Length)
        d = longest.Vertexes[1].Point - longest.Vertexes[0].Point
        d.normalize()
        others = [v.Point for v in face.Vertexes]
        p0 = longest.Vertexes[0].Point
        width = max(((q - p0) - d * (q - p0).dot(d)).Length for q in others)
    bb = exact_bbox(shape)
    small = width is not None and width <= 0.2 * min(x for x in (bb.XLength, bb.YLength, bb.ZLength) if x > 1e-6)
    if is45 and small:
        return "chamfer", width / math.sqrt(2)
    return "incline", width


def cone_chamfers(shape):
    """Short 45° cones on shafts/holes (turned chamfers): ``[(axis, leg)]``."""
    out = []
    for f in shape.Faces:
        s = f.Surface
        if type(s).__name__ != "Cone":
            continue
        ax = _axis_name(s.Axis)
        if not ax or abs(abs(math.degrees(s.SemiAngle)) - 45) > 0.1:
            continue
        i = "XYZ".index(ax)
        ext = [getattr(f.BoundBox, ax + "Min"), getattr(f.BoundBox, ax + "Max")]
        leg = ext[1] - ext[0]
        if leg > 0:
            out.append((ax, leg, f))
    return out


# -------------------------------------------------------------- threads
def _on_line(axis, a, b, tol=1e-2):
    i = "XYZ".index(axis)
    return all(abs(a[k] - b[k]) < tol for k in range(3) if k != i)


def _overlaps(e1, e2, tol=1e-3):
    return e1[0] <= e2[1] + tol and e2[0] <= e1[1] + tol


def thread_of_hole(h, threads):
    """Internal thread drawn as hole ``h`` (hole at the minor diameter), or None."""
    for t in threads or ():
        if (t["internal"] and t["axis"] == h["axis"] and _on_line(h["axis"], t["center"], h["center"])
                and abs(t["minor_dia"] - h["dia"]) < 0.05 and _overlaps(t["extent"], h["extent"])):
            return t
    return None


def threads_of_cylinder(c, threads):
    """External threads drawn as the outer cylinder ``c`` (at the nominal major diameter)."""
    out = []
    for t in threads or ():
        if t["internal"] or t["axis"] != c["axis"] or not _on_line(c["axis"], t["center"], c["center"]):
            continue
        d = t["nominal"] or t["major_dia"]
        if (abs(d - 2 * c["radius"]) < 0.05 or abs(t["major_dia"] - 2 * c["radius"]) < 0.1) and _overlaps(t["extent"], c["extent"]):
            out.append(t)
    return out


def thread_key(t):
    """Threads with the same key share one callout (``2× M8×1.25-6H ↧12``)."""
    if t is None:
        return None
    return (t["designation"], t["internal"], t["handed"], t["through"], _r(t["length"], 2) if t["internal"] else None)


# -------------------------------------------------------------- top level
def requirements(shape, turned_axis=None, assembly=False, threads=None):
    """All dimensioning requirements for ``shape`` (aligned to X/Y/Z).

    ``threads`` (from :func:`threads.detect_threads`, with ``shape`` already
    simplified by :func:`threads.simplify`) turns the matching hole / outer
    diameter callouts into thread callouts and adds the thread end positions.
    """
    bb = exact_bbox(shape)
    lo = {"X": bb.XMin, "Y": bb.YMin, "Z": bb.ZMin}
    hi = {"X": bb.XMax, "Y": bb.YMax, "Z": bb.ZMax}
    reqs = []
    threads = list(threads or [])
    used_threads = set()

    def loc(axis, coord, source, needs_center=False, prio=1, **extra):
        if abs(coord - lo[axis]) < TOL:
            return  # the datum itself
        r = {"kind": "loc", "axis": axis, "coord": _r(coord), "datum": _r(lo[axis]),
             "source": source, "center": needs_center, "prio": prio}
        r.update(extra)
        reqs.append(r)

    if assembly:
        for a in "XYZ":
            if hi[a] - lo[a] > TOL:
                loc(a, hi[a], "overall", prio=3)
        return reqs, {"datums": lo}

    cyls = cylinders(shape)
    # --- holes, patterns ---------------------------------------------------
    stacks = hole_stacks(shape, cyls)
    groups = defaultdict(list)
    for h in stacks:
        h["thread"] = thread_of_hole(h, threads)
        key = (h["axis"], _r(h["dia"], 2), h["through"], _r(h["depth"], 2), h["cbore"] and tuple(_r(v, 2) for v in h["cbore"]),
               h["csk"] and tuple(_r(v, 1) for v in h["csk"]), thread_key(h["thread"]))
        groups[key].append(h)
    hole_groups = [{"axis": k[0], "dia": k[1], "items": v} for k, v in groups.items()]
    patterns = bolt_circles(hole_groups)
    in_pattern = {id(h) for p in patterns for h in p["group"]["items"]}
    outer_axes = [c for c in cyls if not c["internal"] and c["full"] and c["axis"]]

    def concentric(axis, center):
        """On the axis of an outer cylinder (tube bore, flange bore, PCD centre)."""
        i = "XYZ".index(axis)
        return any(o["axis"] == axis and all(abs(o["center"][k] - center[k]) < 1e-3 for k in range(3) if k != i)
                   for o in outer_axes)
    for g in sorted(hole_groups, key=lambda g: -g["dia"]):
        h0 = g["items"][0]
        pat = next((p for p in patterns if p["group"] is g), None)
        t0 = h0.get("thread")
        if t0 is not None:
            # internal thread: the tap-drill hole carries the thread callout instead of ⌀ [ISO 6410]
            used_threads.update(id(h["thread"]) for h in g["items"])
            text = threads_mod.callout(t0, len(g["items"])) + (" EQ SP" if pat else "")
            reqs.append({"kind": "dia", "feature": "thread", "internal": True, "axis": g["axis"], "dia": g["dia"],
                         "centers": [h["center"] for h in g["items"]], "text": text, "count": len(g["items"]),
                         "prio": 3, "thread": threads_mod.summary(t0)})
        else:
            text = hole_text(h0, len(g["items"])) + (" EQ SP" if pat else "")
            reqs.append({"kind": "dia", "feature": "hole", "axis": g["axis"], "dia": g["dia"],
                         "centers": [h["center"] for h in g["items"]], "text": text, "count": len(g["items"]), "prio": 3})
        for h in g["items"]:
            if id(h) in in_pattern or concentric(g["axis"], h["center"]):
                continue
            for a in "XYZ":
                if a != g["axis"]:
                    loc(a, _coord(h["center"], a), "hole-center", needs_center=True, prio=2)
    for p in patterns:
        reqs.append({"kind": "pcd", "axis": p["axis"], "center": p["center"], "dia": p["pcd"],
                     "start_angle": p["start_angle"], "prio": 3})
        if not concentric(p["axis"], p["center"]):
            for a in "XYZ":
                if a != p["axis"]:
                    loc(a, _coord(p["center"], a), "pattern-center", needs_center=True, prio=2)

    # --- bosses / shafts / turned diameters -------------------------------
    seen_ext = set()
    for c in cyls:
        if c["internal"] or not c["axis"]:
            continue
        if not c["full"]:
            continue
        key = (c["axis"], _r(c["radius"], 3)) + tuple(_r(c["center"][k], 2) for k in range(3) if "XYZ"[k] != c["axis"])
        if key in seen_ext:
            continue
        seen_ext.add(key)
        on = threads_of_cylinder(c, threads)
        if on:
            # external thread: the outline at the major diameter carries the callout [ISO 6410]
            used_threads.update(id(t) for t in on)
            reqs.append({"kind": "dia", "feature": "thread", "internal": False, "axis": c["axis"],
                         "dia": 2 * c["radius"], "centers": [c["center"]], "text": threads_mod.callout(on[0], len(on)),
                         "count": len(on), "prio": 3, "extent": c["extent"], "thread": threads_mod.summary(on[0])})
        else:
            reqs.append({"kind": "dia", "feature": "turned" if turned_axis == c["axis"] else "boss", "axis": c["axis"],
                         "dia": 2 * c["radius"], "centers": [c["center"]], "text": "⌀%.2w", "count": 1, "prio": 3,
                         "extent": c["extent"]})
        if turned_axis != c["axis"]:
            for a in "XYZ":
                if a == c["axis"]:
                    continue
                ca = _coord(c["center"], a)
                # a cylinder that forms the outer boundary is located by its ⌀ and the overall size
                if abs(ca - c["radius"] - lo[a]) < TOL or abs(ca + c["radius"] - hi[a]) < TOL:
                    continue
                loc(a, ca, "boss-center", needs_center=True, prio=2)

    # --- threads ---------------------------------------------------------------
    for t in threads:
        if not t["internal"]:
            # thread length: both ends located along the axis; where no edge marks the
            # end (thread runs out on a plain cylinder) the planner adds a cosmetic vertex
            for coord in t["extent"]:
                p3 = list(t["center"])
                p3["XYZ".index(t["axis"])] = coord
                loc(t["axis"], coord, "thread-end", prio=2, point3d=tuple(p3), radial=t["minor_dia"] / 2.0)
        if id(t) in used_threads:
            continue
        # a thread whose plain cylinder is not a full surface: callout on its own
        d = t["minor_dia"] if t["internal"] else (t["nominal"] or t["major_dia"])
        reqs.append({"kind": "dia", "feature": "thread", "internal": t["internal"], "axis": t["axis"], "dia": d,
                     "centers": [t["center"]], "text": threads_mod.callout(t), "count": 1, "prio": 3,
                     "extent": t["extent"], "thread": threads_mod.summary(t)})

    # --- fillets / partial arcs --------------------------------------------
    radii = defaultdict(int)
    slot_ends = []
    for c in cyls:
        if c["full"] or not c["axis"]:
            continue
        radii[(_r(c["radius"], 3), c["axis"])] += 1
        if c.get("span", 0) >= math.pi - 1e-3:
            # slot ends / rounded ends: the arc centre must be located
            for a in "XYZ":
                if a != c["axis"]:
                    slot_ends.append((a, _coord(c["center"], a)))
    for a, coord in slot_ends:
        loc(a, coord, "arc-center", needs_center=True, prio=2)
    for f in shape.Faces:
        if type(f.Surface).__name__ == "Toroid":
            ax = _axis_name(f.Surface.Axis)
            radii[(_r(f.Surface.MinorRadius, 3), ax)] += 1
    for (r, ax), n in sorted(radii.items()):
        reqs.append({"kind": "radius", "axis": ax, "radius": r, "count": n, "prio": 2,
                     "text": (f"{n}× " if n > 1 else "") + "R%.2w"})

    # --- spheres -------------------------------------------------------------
    for f in shape.Faces:
        if type(f.Surface).__name__ == "Sphere":
            reqs.append({"kind": "sphere", "radius": float(f.Surface.Radius), "center": tuple(f.Surface.Center),
                         "text": "SR%.2w", "prio": 2})

    # --- planar faces ------------------------------------------------------------
    pos, inclined = plane_positions(shape)
    for a in "XYZ":
        for coord in sorted(pos[a]):
            loc(a, coord, "face", prio=3 if abs(coord - hi[a]) < TOL else 1)
    chamfers = defaultdict(int)
    for f in inclined:
        kind, size = classify_inclined(f, shape)
        if kind == "chamfer":
            chamfers[_r(size, 2)] += 1
            continue
        reqs.append({"kind": "angle", "face": f, "prio": 2})
        for e in f.Edges:
            if type(e.Curve).__name__ != "Line":
                continue
            d = e.Vertexes[1].Point - e.Vertexes[0].Point
            ea = _axis_name(d)
            if not ea:
                continue
            p = e.Vertexes[0].Point
            for a in "XYZ":
                if a != ea:
                    loc(a, _coord((p.x, p.y, p.z), a), "incline-edge", prio=1)
    for ax, leg, f in cone_chamfers(shape):
        chamfers[_r(leg, 2)] += 1
    for leg, n in sorted(chamfers.items()):
        reqs.append({"kind": "chamfer", "leg": leg, "count": n, "prio": 2,
                     "text": (f"{n}× " if n > 1 else "") + f"C{_fmt(leg)}"})

    # overall extents must always exist, unless an outer ⌀ already spans them
    def spanned_by_diameter(a):
        i = "XYZ".index(a)
        return any(c["axis"] != a and not c["internal"] and c["full"] and c["axis"]
                   and abs(c["center"][i] - c["radius"] - lo[a]) < TOL and abs(c["center"][i] + c["radius"] - hi[a]) < TOL
                   for c in cyls)

    for a in "XYZ":
        if spanned_by_diameter(a):
            continue
        if hi[a] - lo[a] > TOL and not any(r["kind"] == "loc" and r["axis"] == a and abs(r["coord"] - _r(hi[a])) < TOL for r in reqs):
            loc(a, hi[a], "overall", prio=3)
    # de-duplicate locations (same axis + coordinate) keeping the strongest source
    best = {}
    others = []
    for r in reqs:
        if r["kind"] != "loc":
            others.append(r)
            continue
        k = (r["axis"], r["coord"])
        if k not in best or r["prio"] > best[k]["prio"] or (r["center"] and not best[k]["center"]):
            best[k] = r
    return others + list(best.values()), {"datums": lo, "max": hi}
