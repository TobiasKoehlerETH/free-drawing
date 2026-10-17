"""Automatic section views and enlarged detail views [03][CHECKLIST D].

Decide *whether* a drawing needs a section or a detail, create the TechDraw
objects headless, describe them for the snapshot / renderer, and find free
space on the sheet for them.

When is what needed (guidelines/03, "Project rules for automatic drawings"):

* **Section** - an internal feature would otherwise be shown or dimensioned
  only with hidden lines: blind holes (depth), counterbores / countersinks,
  internal pockets / steps, bores of hollow turned parts (tube, flange) and
  intersecting (cross) holes. The cutting plane is a principal plane through
  the feature axis; solid shafts are never cut longitudinally [03 rule 3].
* **Detail** - a feature is smaller than ~3 mm on paper at the sheet scale
  (chamfer, radius, groove, hem, step) or several dimensions crowd a ~10 mm
  spot. Enlarged at a preferred scale 2:1 / 5:1 / 10:1, caption ``X (5:1)``.

Public API (all coordinates: page mm, origin bottom-left, Y up, unless the
name says *local* = base view-local, unscaled, centred, Y up)::

    decide_sections(shape, view_geoms, specs=(), reqs=(), info=None,
                    max_sections=2, used_letters=(), projection="first") -> [plan]
    add_section(doc, page, base_view, plan, scale, x=None, y=None) -> DrawViewSection
    decide_details(view_geoms, specs, scale, used_letters=(), max_details=3) -> [plan]
    add_detail(doc, page, base_view, plan, x=None, y=None) -> DrawViewDetail
    place_extra_views(page, views, keepouts=None, frame=None) -> [(x, y) | None]
    add_sections_and_details(doc, page, shape, view_geoms, specs, reqs, info,
                             scale, keepouts=None, projection="first") -> dict
    view_center3d(view) -> App.Vector   # model point at a view's local (0, 0)
    section_record(view) / detail_record(view) -> dict   # used by snapshot.py
    used_letters(page) -> set

Section plan (dict, JSON-able except nothing - all plain values)::

    {"kind": "section", "letter": "A", "caption": "A–A",
     "base": "Top",              # ViewGeom.name of the view carrying the cutting line
     "base_view": "ProjItem001", # its TechDraw object name
     "like": "Front" | None,     # existing view the section looks like (same Direction)
     "normal": [0, -1, 0],       # SectionNormal == Direction; material on this side is removed
     "x_direction": [1, 0, 0], "origin": [x, y, z],
     "plane_axis": "Y", "coord": 25.0,
     "half": False, "half_up": [0, 0, 1] | None, "axis_point": [x, y, z] | None,
     "side": "right" | "left" | "top" | "bottom",   # preferred place next to the base view
     "features": ["counterbore ⌀6.6/⌀11 at (20, 25, 25)", ...], "reasons": [...]}

Detail plan::

    {"kind": "detail", "letter": "B", "caption": "B (5:1)",
     "base": "Right", "base_view": "ProjItem001",
     "anchor": [x, y],          # base view-local, unscaled (TechDraw AnchorPoint)
     "radius": 6.5,             # model mm (TechDraw Radius)
     "scale": 5.0,              # absolute view scale (TechDraw Scale)
     "items": [...], "reasons": [...]}

What builder.create_drawing should call, and when
-------------------------------------------------

Simple post-pass (no change to dimension planning): after the main views,
dimensions, notes and the isometric are placed and ``doc.recompute()`` ran::

    res = sections.add_sections_and_details(doc, page, shape, geoms, specs, reqs, info, s,
                                            keepouts=[tb, iso_box, notes_box], projection=projection)

It decides, creates and places the views in free sheet space and returns
``{"sections": [names], "details": [names], "plans": [...], "notes": [...],
"unplaced": [...]}``.

Better (dimensions of internal features on the section, not on hidden
lines - guideline 03 project rule): call :func:`decide_sections` right after
the first ``_plan()``; create each section with :func:`add_section` (no x/y
yet), then add ``dimensions.ViewGeom(sec, view_center3d(sec), plan["caption"])``
to the view geoms before ``planner.realize`` so hidden-line specs move to the
section; include the section's box in the layout (or use
:func:`place_extra_views` after the scale search). If ``plan["like"]`` is a
view whose only job was to show those internal features, the section may
replace it (remove that projection) - the builder decides.
"""

import json
import math
import os

import FreeCAD as App
import Part

from . import analysis, features
from . import geometry as G

LETTERS = [c for c in "ABCDEFGHJKLMNPRTUVWY"]  # I O Q S X Z are not used [03][11]
SECTION_PROP = "FDSection"
DETAIL_PROP = "FDDetail"

SMALL_PAPER = 3.0  # mm: features smaller than this on paper need a detail [03 project rule]
TARGET_PAPER = 8.0  # mm: smallest feature size wanted inside the detail
CROWD_PAPER = 10.0  # mm: several dimension targets inside this spot = crowded
DETAIL_MAX_PAPER = 70.0  # mm: largest detail circle diameter on paper
DETAIL_SCALES = [2.0, 5.0, 10.0, 20.0]
HATCH_PATTERN = "Diagonal4"  # FCPAT.pat: 45 deg, 4 mm; HatchScale 0.75 -> 3 mm [03 rule 2]
HATCH_SCALE = 0.75
CAPTION_ROOM = 9.0  # mm below a section/detail view for its caption
DIM_ROOM = 14.0  # mm around a new view kept free for its later dimensions

AX = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}


# ----------------------------------------------------------------- helpers
def _v(seq):
    return App.Vector(float(seq[0]), float(seq[1]), float(seq[2]))


def _l(vec, nd=6):
    return [round(float(vec.x), nd), round(float(vec.y), nd), round(float(vec.z), nd)]


def _coord(p, axis):
    i = "XYZ".index(axis)
    return float(p[i]) if not hasattr(p, "x") else float((p.x, p.y, p.z)[i])


def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def ratio_text(scale):
    """ISO 5455 scale text without spaces: '5:1', '1:2'."""
    if scale >= 1:
        v = round(scale, 3)
        return f"{int(v) if v == int(v) else v}:1"
    inv = round(1.0 / scale, 3)
    return f"1:{int(inv) if inv == int(inv) else inv}"


def used_letters(page):
    """Section symbols and detail references already on ``page``."""
    out = set()
    for o in page.Views:
        if o.TypeId in ("TechDraw::DrawViewSection", "TechDraw::DrawComplexSection"):
            out.add(str(o.SectionSymbol))
        elif o.TypeId == "TechDraw::DrawViewDetail":
            out.add(str(o.Reference))
    return {x for x in out if x}


def next_letters(used, n):
    used = set(used)
    out = []
    for c in LETTERS:
        if c not in used:
            out.append(c)
            used.add(c)
        if len(out) == n:
            return out
    k = 1
    while len(out) < n:  # A1, B1, ... after the alphabet is exhausted
        for c in LETTERS:
            t = f"{c}{k}"
            if t not in used:
                out.append(t)
                if len(out) == n:
                    break
        k += 1
    return out


def _vg_axis(vg):
    return G.axis_name(App.Vector(vg.direction))


def _source_center(view):
    shapes = []
    for src in list(getattr(view, "Source", []) or []) + list(getattr(view, "XSource", []) or []):
        try:
            shapes.append(src.Shape)
        except Exception:
            pass
    if not shapes:
        return App.Vector(0, 0, 0)
    bb = App.BoundBox(G.exact_bbox(shapes[0]))
    for s in shapes[1:]:
        bb.add(G.exact_bbox(s))
    return bb.Center


def _source_shape(view):
    shapes = []
    for src in list(getattr(view, "Source", []) or []):
        try:
            if not src.Shape.isNull():
                shapes.append(src.Shape)
        except Exception:
            pass
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def _frame(view):
    d = App.Vector(view.Direction)
    x = App.Vector(view.XDirection)
    if x.Length < 1e-9:
        x = App.Vector(1, 0, 0)
    return d, x, G.up_vector(d, x)


# ------------------------------------------------------- plane geometry
def _plane_basis(n, half_up=None):
    n = App.Vector(n)
    n.normalize()
    if half_up is not None:
        u = App.Vector(half_up)
    else:
        u = App.Vector(1, 0, 0) if abs(n.x) < 0.9 else App.Vector(0, 1, 0)
        u = u - n * u.dot(n)
    u.normalize()
    w = n.cross(u)
    w.normalize()
    return n, u, w


def _plane_face(shape, normal, origin, half_up=None, axis_point=None):
    """Planar face covering the shape's cut (or the sectioned half of it)."""
    bb = G.exact_bbox(shape)
    L = bb.DiagonalLength + 20.0
    n, u, w = _plane_basis(normal, half_up)
    o = App.Vector(origin)
    c = App.Vector(bb.Center)
    p0 = c + n * (o - c).dot(n)  # bbox centre on the plane
    if half_up is not None and axis_point is not None:
        a = App.Vector(axis_point)
        a = a + n * (o - a).dot(n)
        base = a + w * w.dot(p0 - a)
        pts = [base - w * L, base + w * L, base + w * L + u * L, base - w * L + u * L]
    else:
        pts = [p0 - u * L - w * L, p0 + u * L - w * L, p0 + u * L + w * L, p0 - u * L + w * L]
    return Part.Face(Part.makePolygon(pts + [pts[0]]))


def cut_faces(shape, normal, origin, half_up=None, axis_point=None):
    """Faces of material in the cutting plane (what gets hatched)."""
    face = _plane_face(shape, normal, origin, half_up, axis_point)
    try:
        com = shape.common(face)
        faces = [f for f in com.Faces if f.Area > 1e-6]
        if faces:
            return faces
    except Exception:
        pass
    try:  # fallback: slice wires
        n = App.Vector(normal)
        n.normalize()
        wires = shape.slice(n, n.dot(App.Vector(origin)))
        if wires:
            f = Part.makeFace(wires, "Part::FaceMakerBullseye")
            return [x for x in f.Faces]
    except Exception:
        pass
    return []


def remaining_shape(shape, normal, origin, half_up=None, axis_point=None):
    """The part after removing the material on the +normal side (viewer side)."""
    face = _plane_face(shape, normal, origin, half_up, axis_point)
    n = App.Vector(normal)
    n.normalize()
    L = G.exact_bbox(shape).DiagonalLength + 20.0
    try:
        tool = face.extrude(n * L)
        rest = shape.cut(tool)
        if rest.isNull() or not rest.Solids:
            return shape
        return rest
    except Exception:
        return shape


# --------------------------------------------------------------- features
def _internal_features(shape, view_geoms, specs, info):
    """Internal features that need a section.

    Each: ``{"desc", "axis" (feature axis or None), "planes": [(normal_axis, coord)],
    "weight", "kind", "center"}``.
    """
    info = info or {}
    cyls = analysis.cylinders(shape)
    stacks = features.hole_stacks(shape, cyls)
    turned_axis = info.get("axis") if info.get("kind") == "turned" else None
    outer = [c for c in cyls if not c["internal"] and c["full"] and c["axis"]]
    inner_full = [c for c in cyls if c["internal"] and c["full"] and c["axis"]]
    feats = []

    def coaxial(c, axis, center):
        i = "XYZ".index(axis)
        return c["axis"] == axis and all(abs(c["center"][k] - center[k]) < 1e-3 for k in range(3) if k != i)

    # shafts: turned parts without a bore are never cut longitudinally [03 rule 3]
    hollow_axes = set()
    for c in inner_full:
        if any(coaxial(o, c["axis"], c["center"]) and o["radius"] > c["radius"] + 1e-6 for o in outer):
            hollow_axes.add((c["axis"],) + tuple(round(c["center"][k], 2) for k in range(3) if "XYZ"[k] != c["axis"]))
    is_shaft = bool(turned_axis) and not any(k[0] == turned_axis for k in hollow_axes)

    def planes_through(axis, center):
        out = []
        for n in "XYZ":
            if n == axis:
                continue
            if is_shaft and n != turned_axis:
                continue  # would cut the shaft longitudinally
            out.append((n, round(float(center["XYZ".index(n)]), 4)))
        return out

    notes = []
    for h in stacks:
        key = (h["axis"],) + tuple(round(h["center"][k], 2) for k in range(3) if "XYZ"[k] != h["axis"])
        bore = key in hollow_axes
        why = []
        if h["cbore"]:
            why.append(f"counterbore ⌀{_fmt(h['cbore'][0])} ↧{_fmt(h['cbore'][1])}")
        if h["csk"]:
            why.append(f"countersink ⌀{_fmt(h['csk'][0])}×{_fmt(h['csk'][1])}°")
        if not h["through"]:
            why.append(f"blind depth ↧{_fmt(h['depth'])}")
        if bore:
            why.append(f"bore ⌀{_fmt(h['dia'])} of a hollow round part")
        if not why:
            continue
        planes = planes_through(h["axis"], h["center"])
        if is_shaft and h["axis"] == turned_axis:
            notes.append(f"axial hole ⌀{_fmt(h['dia'])} in a solid shaft: use a local (broken-out) section, not a full section [03 rule 3]")
            continue
        if not planes:
            continue
        feats.append({"desc": f"⌀{_fmt(h['dia'])} " + ", ".join(why) + " at (" + ", ".join(_fmt(v) for v in h["center"]) + ")",
                      "axis": h["axis"], "center": tuple(h["center"]), "planes": planes,
                      "weight": 3.0 if bore else 2.0, "kind": "bore" if bore else "hole"})
    # cross holes: two internal cylinders whose axes intersect
    for i, a in enumerate(inner_full):
        for b in inner_full[i + 1:]:
            if a["axis"] == b["axis"]:
                continue
            n = next(k for k in "XYZ" if k not in (a["axis"], b["axis"]))
            ia, ib = "XYZ".index(a["axis"]), "XYZ".index(b["axis"])
            ni = "XYZ".index(n)
            if abs(a["center"][ni] - b["center"][ni]) > 1e-3:
                continue
            # a's axis passes through b's axial extent and vice versa
            if not (b["extent"][0] - 1e-3 <= a["center"][ib] <= b["extent"][1] + 1e-3):
                continue
            if not (a["extent"][0] - 1e-3 <= b["center"][ia] <= a["extent"][1] + 1e-3):
                continue
            if is_shaft and n != turned_axis:
                continue
            feats.append({"desc": f"cross holes ⌀{_fmt(2 * a['radius'])} ({a['axis']}) × ⌀{_fmt(2 * b['radius'])} ({b['axis']})",
                          "axis": a["axis"], "center": tuple(a["center"]), "planes": [(n, round(float(a["center"][ni]), 4))],
                          "weight": 2.0, "kind": "cross"})
    # dimensions that could only be placed on hidden lines
    for sp in specs or ():
        if not sp.get("hidden"):
            continue
        vg = sp["view"]
        n = _vg_axis(vg)
        if not n or (is_shaft and n != turned_axis):
            continue
        pts = _spec_points(vg, sp)
        depth = _hidden_depth(shape, vg, pts, cyls)
        if depth is None:
            continue
        feats.append({"desc": f"dimension {sp.get('role', sp['type'])} on hidden lines in {vg.name}",
                      "axis": None, "center": None, "planes": [(n, round(depth, 4))], "weight": 1.5,
                      "kind": "hidden", "view": vg.name})
    return feats, notes, is_shaft


def _spec_points(vg, sp):
    pts = []
    for r in sp.get("refs") or ():
        if r.startswith("Vertex"):
            i = int(r[6:])
            for k, p in vg.vertices:
                if k == i:
                    pts.append(p)
                    break
        elif r.startswith("Edge") and sp.get("circle"):
            pts.append(tuple(sp["circle"]["center"]))
    return pts


def _hidden_depth(shape, vg, pts, cyls, tol=2e-3):
    """Depth (model coordinate along the view axis) of the hidden feature at ``pts``."""
    n = _vg_axis(vg)
    if not n or not pts:
        return None
    c3 = vg.center3d
    xa, xs = vg.model_axis("x")
    ya, ys = vg.model_axis("y")
    if not xa or not ya:
        return None
    bb = G.exact_bbox(shape)
    lo, hi = getattr(bb, n + "Min"), getattr(bb, n + "Max")
    depths = []
    for p in pts:
        mx = _coord(c3, xa) + xs * p[0]
        my = _coord(c3, ya) + ys * p[1]
        # internal cylinders whose silhouette contains the point -> their axis depth
        for c in cyls:
            if not c["internal"] or not c["axis"] or c["axis"] == n:
                continue
            ax = c["axis"]
            other = ya if ax == xa else xa
            along = mx if ax == xa else my
            across = my if ax == xa else mx
            if c["extent"][0] - tol <= along <= c["extent"][1] + tol and abs(across - _coord(c["center"], other)) <= c["radius"] + tol:
                depths.append((_coord(c["center"], n), _coord(c["center"], n)))
        # B-rep vertices projecting onto the point
        hits = []
        for v in shape.Vertexes:
            q = v.Point
            if abs(_coord(q, xa) - mx) < tol and abs(_coord(q, ya) - my) < tol:
                hits.append(_coord(q, n))
        hits = [h for h in hits if lo + tol < h < hi - tol] or hits
        if len(hits) >= 2:
            depths.append((min(hits), max(hits)))
    if not depths:
        return None
    # the interval shared by most hits; cut through its middle
    a = max(d[0] for d in depths)
    b = min(d[1] for d in depths)
    if a <= b + tol:
        return 0.5 * (a + b)
    d0 = depths[0]
    return 0.5 * (d0[0] + d0[1])


# ------------------------------------------------------------- decisions
def decide_sections(shape, view_geoms, specs=(), reqs=(), info=None, max_sections=2, used_letters=(),
                    projection="first"):
    """Section plans for internal features that would otherwise be hidden-only.

    ``view_geoms``: :class:`dimensions.ViewGeom` list of the orthographic views
    (``.name`` 'Front'/'Top'/..., ``.view`` the TechDraw object). ``specs`` /
    ``reqs``: planner specs (``hidden`` flags) and feature requirements.
    ``info``: :func:`analysis.classify` result. Notes about skipped cases
    (shafts) are appended to ``info["section_notes"]`` when ``info`` is a dict.
    """
    info = info if info is not None else {}
    if len(shape.Solids) > 1:
        info.setdefault("section_notes", []).append(
            "assembly section needs component exclusions and distinct hatching; add a reviewed section explicitly")
        return []
    vgs = [vg for vg in view_geoms if _vg_axis(vg)]
    if not vgs:
        return []
    feats, notes, is_shaft = _internal_features(shape, vgs, specs, info)
    if isinstance(info, dict):
        info.setdefault("section_notes", []).extend(notes)
    if not feats:
        return []
    # candidate planes: (normal axis, coordinate) -> features it cuts
    cand = {}
    for fi, f in enumerate(feats):
        for n, coord in f["planes"]:
            key = None
            for k in cand:
                if k[0] == n and abs(k[1] - coord) < 1e-3:
                    key = k
                    break
            cand.setdefault(key or (n, coord), set()).add(fi)
    front = next((vg for vg in vgs if vg.name == "Front"), vgs[0])
    plans = []
    covered = set()
    letters = next_letters(used_letters, max_sections)
    while len(plans) < max_sections:
        best = None
        for (n, coord), fis in cand.items():
            new = fis - covered
            if not new:
                continue
            bases = [vg for vg in vgs if _vg_axis(vg) != n]
            if not bases:
                continue  # no view shows the plane edge-on
            score = (sum(feats[i]["weight"] for i in new),
                     1 if _vg_axis(front) == n else 0,
                     1 if any(_vg_axis(vg) == n for vg in vgs) else 0)
            if best is None or score > best[0]:
                best = (score, n, coord, fis)
        if best is None:
            break
        _, n, coord, fis = best
        covered |= fis
        plan = _make_section_plan(shape, vgs, [feats[i] for i in sorted(fis)], n, coord, info, is_shaft,
                                  letters[len(plans)], projection)
        if plan:
            plans.append(plan)
        else:
            break
    return plans


def _make_section_plan(shape, vgs, feats, n, coord, info, is_shaft, letter, projection):
    bb = G.exact_bbox(shape)
    like = [vg for vg in vgs if _vg_axis(vg) == n]
    like = sorted(like, key=lambda vg: (vg.name != "Front", vg.name))[0] if like else None
    f_axes = [f["axis"] for f in feats if f["axis"]]
    bases = [vg for vg in vgs if _vg_axis(vg) != n]
    prio = {"Front": 3, "Top": 2, "Right": 1, "Left": 1, "Bottom": 0}

    def base_key(vg):
        circles = sum(1 for a in f_axes if _vg_axis(vg) == a)  # features seen as circles
        area = (vg.bbox[2] - vg.bbox[0]) * (vg.bbox[3] - vg.bbox[1])
        return (circles, prio.get(vg.name, 0), area)

    base = max(bases, key=base_key)
    bd = App.Vector(base.direction)
    bx = App.Vector(base.xdir)
    bu = App.Vector(base.up)
    nvec = App.Vector(AX[n])
    if like is not None:
        D = App.Vector(like.direction)
        X = App.Vector(like.xdir)
    else:
        if abs(nvec.dot(bx)) > 0.5:
            D = bx * -1.0  # viewer on the left of the base view, looking right
            X = bu.cross(D)
        else:
            D = App.Vector(bu)  # viewer above the base view, looking down
            X = App.Vector(bx)
    D.normalize()
    X.normalize()
    origin = App.Vector(bb.Center)
    origin["XYZ".index(n)] = coord
    # preferred side next to the base view: first angle opposite the viewer
    vx, vy = D.dot(bx), D.dot(bu)
    if abs(vx) >= abs(vy):
        viewer = "right" if vx > 0 else "left"
    else:
        viewer = "top" if vy > 0 else "bottom"
    opp = {"right": "left", "left": "right", "top": "bottom", "bottom": "top"}
    side = opp[viewer] if projection != "third" else viewer
    # half section: symmetric hollow turned parts [03 rule 4]
    half, half_up, axis_point = False, None, None
    bore = next((f for f in feats if f["kind"] == "bore"), None)
    if (bore and info.get("kind") == "turned" and bore["axis"] == info.get("axis")
            and _axisymmetric_cylinders(shape, bore["axis"], bore["center"])):
        half = True
        half_up = D.cross(X)  # section view's +Y: the upper half is cut
        half_up.normalize()
        axis_point = App.Vector(*bore["center"])
    reasons = sorted({f["kind"] for f in feats})
    return {
        "kind": "section", "letter": letter, "caption": f"{letter}–{letter}",
        "base": base.name, "base_view": base.view.Name,
        "like": like.name if like is not None else None,
        "normal": _l(D), "x_direction": _l(X), "origin": _l(origin),
        "plane_axis": n, "coord": round(coord, 4),
        "half": half, "half_up": _l(half_up) if half_up is not None else None,
        "axis_point": _l(axis_point) if axis_point is not None else None,
        "side": side, "features": [f["desc"] for f in feats],
        "reasons": [{"hole": "blind / counterbored / countersunk hole", "bore": "bore of a hollow round part",
                     "cross": "intersecting holes", "hidden": "dimension on hidden lines"}[r] for r in reasons],
    }


def _axisymmetric_cylinders(shape, axis, center):
    """Conservative symmetry proof for full coaxial cylinders and their shoulders."""
    direction = AX[axis]
    origin = _v(center)
    for face in shape.Faces:
        surface = face.Surface
        kind = type(surface).__name__
        if kind == "Plane":
            if abs(surface.Axis.dot(direction)) < 1 - 1e-6:
                return False
        elif kind == "Cylinder":
            if abs(surface.Axis.dot(direction)) < 1 - 1e-6:
                return False
            if (surface.Center - origin).cross(direction).Length > 1e-4:
                return False
        else:
            return False
    return all(c["full"] for c in analysis.cylinders(shape))


# ---------------------------------------------------------------- create
def _set_prop(obj, name, value):
    if name not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", name, "FreeDrawing", "free-drawing section/detail plan (JSON)")
    setattr(obj, name, value)


def _pat_file():
    p = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "PAT", "FCPAT.pat")
    return p if os.path.isfile(p) else None


def add_section(doc, page, base_view, plan, scale, x=None, y=None, name=None):
    """Create the TechDraw section view for ``plan`` and verify it has geometry.

    Full sections are ``DrawViewSection``; half sections a
    ``DrawComplexSection`` with an L-shaped cutting profile. Hatching is set
    to the ISO 45° pattern (``FCPAT.pat`` *Diagonal4*, 3 mm), cut surfaces
    only. Raises ``RuntimeError`` (and removes the object) when TechDraw
    produces no geometry.
    """
    letter = plan["letter"]
    D = _v(plan["normal"])
    X = _v(plan["x_direction"])
    O = _v(plan["origin"])
    half = bool(plan.get("half"))
    src = list(base_view.Source)
    prof = None
    if half:
        sec = doc.addObject("TechDraw::DrawComplexSection", name or f"Section{letter}")
        shape = _source_shape(base_view)
        R = G.exact_bbox(shape).DiagonalLength + 10.0
        ap = _v(plan["axis_point"])
        ap = ap + D * (O - ap).dot(D)
        up = _v(plan["half_up"])
        prof = doc.addObject("Part::Feature", f"{sec.Name}Profile")
        prof.Shape = Part.makePolygon([ap + D * R, ap, ap + up * R])
        prof.Visibility = False
        sec.CuttingToolWireObject = prof
        sec.ProjectionStrategy = "Offset"
    else:
        sec = doc.addObject("TechDraw::DrawViewSection", name or f"Section{letter}")
    page.addView(sec)
    sec.BaseView = base_view
    sec.Source = src
    try:
        sec.XSource = list(getattr(base_view, "XSource", []) or [])
    except Exception:
        pass
    sec.SectionNormal = D
    sec.Direction = D
    sec.XDirection = X
    sec.SectionOrigin = O
    sec.SectionSymbol = letter
    sec.SectionDirection = "Aligned"
    sec.ScaleType = "Custom"
    sec.Scale = float(scale)
    try:
        sec.FuseBeforeCut = sum(len(s.Shape.Solids) for s in src) > 1
    except Exception:
        pass
    sec.HardHidden = False  # no hidden lines in sections
    sec.SmoothVisible = False
    sec.SeamVisible = False
    pat = _pat_file()
    if pat:
        sec.CutSurfaceDisplay = "PatHatch"
        sec.FileGeomPattern = pat
        sec.NameGeomPattern = HATCH_PATTERN
        sec.HatchScale = HATCH_SCALE
    else:
        sec.CutSurfaceDisplay = "SvgHatch"
    sec.Caption = plan.get("caption") or f"{letter}–{letter}"
    sec.Label = f"Section {letter}–{letter}"
    vo = getattr(sec, "ViewObject", None)
    if vo is not None:
        for prop, val in (("LineWidth", 0.7), ("HiddenWidth", 0.35), ("ExtraWidth", 0.35), ("ArcCenterMarks", False)):
            try:
                setattr(vo, prop, val)
            except Exception:
                pass
    if x is not None and y is not None:
        sec.X, sec.Y = float(x), float(y)
    _set_prop(sec, SECTION_PROP, json.dumps({k: plan[k] for k in plan if k not in ("features",)}, ensure_ascii=False))
    doc.recompute()
    ok = False
    try:
        ok = sec.isValid() and len(sec.getVisibleEdges()) > 0
    except Exception:
        ok = False
    if not ok:
        status = sec.getStatusString() if hasattr(sec, "getStatusString") else "?"
        doc.removeObject(sec.Name)
        if prof is not None:
            doc.removeObject(prof.Name)
        doc.recompute()
        raise RuntimeError(f"section {letter}–{letter} produced no geometry ({status})")
    return sec


def add_detail(doc, page, base_view, plan, x=None, y=None, name=None):
    """Create the TechDraw detail view for ``plan`` (caption ``B (5:1)``)."""
    letter = plan["letter"]
    det = doc.addObject("TechDraw::DrawViewDetail", name or f"Detail{letter}")
    page.addView(det)
    det.BaseView = base_view
    det.Source = list(base_view.Source)
    try:
        det.XSource = list(getattr(base_view, "XSource", []) or [])
    except Exception:
        pass
    det.Direction = App.Vector(base_view.Direction)
    det.XDirection = App.Vector(base_view.XDirection)
    det.AnchorPoint = App.Vector(float(plan["anchor"][0]), float(plan["anchor"][1]), 0.0)
    det.Radius = float(plan["radius"])
    det.Reference = letter
    det.ScaleType = "Custom"
    det.Scale = float(plan["scale"])
    det.HardHidden = bool(getattr(base_view, "HardHidden", False))
    det.SmoothVisible = False
    det.SeamVisible = False
    det.Caption = plan.get("caption") or f"{letter} ({ratio_text(plan['scale'])})"
    det.Label = f"Detail {letter}"
    vo = getattr(det, "ViewObject", None)
    if vo is not None:
        for prop, val in (("LineWidth", 0.7), ("HiddenWidth", 0.35), ("ExtraWidth", 0.35), ("ArcCenterMarks", False)):
            try:
                setattr(vo, prop, val)
            except Exception:
                pass
    if x is not None and y is not None:
        det.X, det.Y = float(x), float(y)
    _set_prop(det, DETAIL_PROP, json.dumps({k: plan[k] for k in plan if k not in ("items",)}, ensure_ascii=False))
    doc.recompute()
    ok = False
    try:
        ok = det.isValid() and len(det.getVisibleEdges()) + len(det.getHiddenEdges()) > 0
    except Exception:
        ok = False
    if not ok:
        status = det.getStatusString() if hasattr(det, "getStatusString") else "?"
        doc.removeObject(det.Name)
        doc.recompute()
        raise RuntimeError(f"detail {letter} produced no geometry ({status})")
    return det


# -------------------------------------------------------------- details
def _spec_items(vg, sp, scale):
    """(model size, [local points], label) of a planned dimension, or None."""
    t = sp["type"]
    role = str(sp.get("role", ""))
    if t in ("DistanceX", "DistanceY") and sp.get("span"):
        size = abs(sp["span"][1] - sp["span"][0])
        pts = _spec_points(vg, sp)
        return size, pts, f"{role or t} {_fmt(size)}"
    if t == "Radius" and sp.get("circle"):
        c = sp["circle"]
        r = float(c["radius"])
        pts = [tuple(c["center"])]
        iv = c.get("iv") or []
        for a, b in iv[:1]:
            m = 0.5 * (a + b)
            pts.append((c["center"][0] + r * math.cos(m), c["center"][1] + r * math.sin(m)))
        return r, pts, f"R{_fmt(r)}"
    if t == "Diameter" and sp.get("circle") and role != "pcd":
        c = sp["circle"]
        r = float(c["radius"])
        # small holes stay readable with a leader; only tiny ones need a detail
        return (2 * r if 2 * r * scale < 2.0 else 1e9), [tuple(c["center"])], f"⌀{_fmt(2 * r)}"
    return None


def decide_details(view_geoms, specs, scale, used_letters=(), max_details=3):
    """Detail plans where features are too small or dimensions too crowded.

    ``scale`` is the sheet scale of the base views. Each cluster of small
    features in one view gives one detail: anchor at the cluster centre,
    radius covering it with margin, scale the smallest preferred enlargement
    (2:1, 5:1, 10:1) that makes the smallest feature >= ~8 mm on paper while
    the detail circle stays <= ~90 mm.
    """
    scale = float(scale)
    by_view = {}
    for sp in specs or ():
        vg = sp["view"]
        it = _spec_items(vg, sp, scale)
        if it is None or not it[1]:
            continue
        size, pts, label = it
        by_view.setdefault(vg.name, (vg, []))[1].append({"size": size, "pts": pts, "label": label})
    clusters = []
    for name, (vg, items) in by_view.items():
        small = [it for it in items if it["size"] * scale < SMALL_PAPER]
        # crowding: >= 3 short dimensions whose targets share a ~10 mm spot
        short = [it for it in items if it["size"] * scale < 2 * TARGET_PAPER and it not in small]
        for it in short:
            mid = _centroid(it["pts"])
            near = [o for o in short + small if o is not it and G.dist(_centroid(o["pts"]), mid) * scale < CROWD_PAPER / 2]
            if len(near) >= 2:
                it["crowded"] = True
                small.append(it)
        if not small:
            continue
        link = 6.0 / scale  # model mm: features closer than 6 mm on paper share a detail
        groups = []
        for it in small:
            hit = [g for g in groups if any(G.dist(p, q) <= link for p in it["pts"] for o in g for q in o["pts"])]
            merged = [it]
            for g in hit:
                merged.extend(g)
                groups.remove(g)
            groups.append(merged)
        for g in groups:
            clusters.append((vg, g))
    if not clusters:
        return []
    clusters.sort(key=lambda c: (-len(c[1]), min(it["size"] for it in c[1])))
    clusters = clusters[:max_details]
    letters = next_letters(used_letters, len(clusters))
    plans = []
    for (vg, items), letter in zip(clusters, letters):
        pts = [p for it in items for p in it["pts"]]
        bb = G.bbox_of(pts)
        anchor = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
        half_diag = 0.5 * math.hypot(bb[2] - bb[0], bb[3] - bb[1])
        fmin = max(min(it["size"] for it in items), 0.05)
        radius = max(half_diag * 1.25 + 0.6, half_diag + 1.0, 2.5 * fmin, 1.5)
        cands = [s for s in ([1.0] if scale < 1 else []) + DETAIL_SCALES if s >= 2 * scale - 1e-9]
        if not cands:
            continue  # no supported enlargement above an already enlarged parent
        fits = [s for s in cands if 2 * radius * s <= DETAIL_MAX_PAPER] or cands[:1]
        want = [s for s in fits if fmin * s >= TARGET_PAPER]
        s_det = want[0] if want else fits[-1]
        # some context around the cluster (up to 2x), a circle >= 3 mm on the parent,
        # all inside the size limit of the enlarged view
        limit = 0.5 * DETAIL_MAX_PAPER / s_det
        radius = max(radius, min(limit, 2.0 * radius, max(radius, 3.0 / scale)))
        plans.append({
            "kind": "detail", "letter": letter, "caption": f"{letter} ({ratio_text(s_det)})",
            "base": vg.name, "base_view": vg.view.Name,
            "anchor": [round(anchor[0], 4), round(anchor[1], 4)], "radius": round(radius, 3), "scale": s_det,
            "items": [it["label"] for it in items],
            "reasons": sorted({"crowded dimensions" if it.get("crowded") else f"feature < {SMALL_PAPER:g} mm on paper" for it in items}),
        })
    return plans


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


# --------------------------------------------------------------- layout
def _occupied(page, pad_dims=2.0):
    """Boxes (page mm) already used by views, dimensions and annotations."""
    from .snapshot import _annotation_record, _dimension_record, page_views

    boxes = []
    for obj in page_views(page):
        t = obj.TypeId
        try:
            if t == "TechDraw::DrawViewDimension":
                rec = _dimension_record(obj)
                if rec.get("text_box"):
                    boxes.append(G.expand(rec["text_box"], pad_dims))
                for ln in rec.get("lines", []):
                    boxes.append(G.expand([min(ln[0], ln[2]), min(ln[1], ln[3]), max(ln[0], ln[2]), max(ln[1], ln[3])], pad_dims))
            elif t in ("TechDraw::DrawViewAnnotation", "TechDraw::DrawRichAnno", "TechDraw::DrawViewBalloon"):
                rec = _annotation_record(obj)
                if rec.get("bbox"):
                    boxes.append(G.expand(rec["bbox"], 2.0))
            elif t == "TechDraw::DrawViewImage":
                ox, oy = G.view_origin(obj)
                w, h = float(obj.Width), float(obj.Height)
                boxes.append([ox - w / 2 - 2, oy - h / 2 - 2, ox + w / 2 + 2, oy + h / 2 + 2])
            elif G.is_type(obj, "TechDraw::DrawViewPart"):
                bb = G.view_page_bbox(obj)
                if obj.TypeId == "TechDraw::DrawViewDetail":
                    ox, oy = G.view_origin(obj)
                    r = _detail_extent(obj)
                    bb = [min(bb[0], ox - r), min(bb[1], oy - r), max(bb[2], ox + r), max(bb[3], oy + r)]
                extra = CAPTION_ROOM if str(getattr(obj, "Caption", "") or "") else 0.0
                boxes.append([bb[0] - 3, bb[1] - 3 - extra, bb[2] + 3, bb[3] + 3])
        except Exception:
            continue
    # cutting-plane labels and detail circles on base views
    for obj in page.Views:
        try:
            if obj.TypeId in ("TechDraw::DrawViewSection", "TechDraw::DrawComplexSection"):
                cl = section_record(obj).get("cutting_line") or {}
                for lt in cl.get("letters", []):
                    x, y = lt["at"]
                    boxes.append([x - 4, y - 4, x + 4, y + 4])
            elif obj.TypeId == "TechDraw::DrawViewDetail":
                dc = detail_record(obj).get("detail_circle") or {}
                if dc:
                    c, r = dc["center"], dc["radius"]
                    boxes.append([c[0] - r - 1, c[1] - r - 1, c[0] + r + 1, c[1] + r + 1])
                    x, y = dc["letter_at"]
                    boxes.append([x - 4, y - 4, x + 4, y + 4])
        except Exception:
            continue
    return boxes


def _sheet(page):
    from . import templates

    W, H = float(page.PageWidth), float(page.PageHeight)
    frame, tb = [10.0, 10.0, W - 10.0, H - 10.0], None
    try:
        sh = templates.parse_template(str(page.Template.Template), W, H)
        frame = sh.get("frame") or frame
        tb = sh.get("title_block")
    except Exception:
        pass
    return frame, tb


def place_extra_views(page, views, keepouts=None, frame=None, step=2.0, margin=6.0):
    """Free page positions (view origins) for new section/detail views.

    ``views``: ``[{"rel_box": [x0, y0, x1, y1] (page mm relative to the view
    origin), "near": [x0, y0, x1, y1] (box of the base view) or None,
    "side": 'right'|'left'|'top'|'bottom'|None, "room": extra mm kept free
    around the view for its dimensions (default 14)}]``. Existing views
    (incl. a caption band), dimensions, notes, the title block, the
    isometric image and ``keepouts`` are avoided. Returns one ``(x, y)`` per
    view, ``None`` when nothing fits. Views are placed in order and later ones
    avoid earlier ones.
    """
    fr, tb = _sheet(page)
    if frame:
        fr = list(frame)
    inner = [fr[0] + margin, fr[1] + margin, fr[2] - margin, fr[3] - margin]
    occ = _occupied(page)
    if tb:
        occ.append(G.expand(tb, 2.0))
    for k in keepouts or ():
        if k:
            occ.append(list(k))
    out = []
    for v in views:
        rb = v["rel_box"]
        room0 = float(v.get("room", DIM_ROOM))
        near = v.get("near")
        side = v.get("side")
        if near:
            ncx, ncy = (near[0] + near[2]) / 2.0, (near[1] + near[3]) / 2.0
        else:
            ncx, ncy = (inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0
        best = None
        for room in (room0, room0 / 2.0, 3.0):  # tighter only when the sheet is full
            need = [rb[0] - room, rb[1] - room - CAPTION_ROOM, rb[2] + room, rb[3] + room]
            x = inner[0] - need[0]
            while x + need[2] <= inner[2]:
                y = inner[1] - need[1]
                while y + need[3] <= inner[3]:
                    box = [x + need[0], y + need[1], x + need[2], y + need[3]]
                    if not any(G.boxes_overlap(box, o) for o in occ):
                        dx, dy = x - ncx, y - ncy
                        cost = math.hypot(dx, dy)
                        if side and near:
                            # prefer the requested side of the base view, roughly aligned with it
                            if side == "right":
                                cost += (0 if dx > 0 else 200) + 0.6 * abs(dy)
                            elif side == "left":
                                cost += (0 if dx < 0 else 200) + 0.6 * abs(dy)
                            elif side == "top":
                                cost += (0 if dy > 0 else 200) + 0.6 * abs(dx)
                            elif side == "bottom":
                                cost += (0 if dy < 0 else 200) + 0.6 * abs(dx)
                        if best is None or cost < best[0]:
                            best = (cost, x, y, room)
                    y += step
                x += step
            if best is not None:
                break
        if best is None:
            out.append(None)
            continue
        _, x, y, room = best
        r2 = room / 2.0
        occ.append([x + rb[0] - r2, y + rb[1] - r2 - CAPTION_ROOM, x + rb[2] + r2, y + rb[3] + r2])
        out.append((round(x, 2), round(y, 2)))
    return out


def _detail_extent(view):
    """Page radius of a detail view's boundary circle (around its origin)."""
    try:
        return float(getattr(view.Radius, "Value", view.Radius)) * float(view.Scale)
    except Exception:
        return 0.0


def _rel_box(view):
    """View box relative to its origin (page mm); details include their boundary circle."""
    ox, oy = G.view_origin(view)
    bb = G.view_page_bbox(view)
    rb = [bb[0] - ox, bb[1] - oy, bb[2] - ox, bb[3] - oy]
    if view.TypeId == "TechDraw::DrawViewDetail":
        r = _detail_extent(view)
        rb = [min(rb[0], -r), min(rb[1], -r), max(rb[2], r), max(rb[3], r)]
    return rb


def add_sections_and_details(doc, page, shape, view_geoms, specs, reqs, info, scale, keepouts=None,
                             projection="first", sections=True, details=True, max_sections=2, max_details=3):
    """Decide, create and place sections and details in one call (post-pass)."""
    info = dict(info or {})
    res = {"sections": [], "details": [], "plans": [], "notes": [], "unplaced": []}
    used = set(used_letters(page))
    by_name = {vg.name: vg for vg in view_geoms}
    if sections:
        plans = decide_sections(shape, view_geoms, specs, reqs, info, max_sections, used, projection)
        res["notes"].extend(info.get("section_notes", []))
        for plan in plans:
            base = by_name[plan["base"]].view
            try:
                sec = add_section(doc, page, base, plan, scale)
            except Exception as e:
                res["notes"].append(f"section {plan['letter']} failed: {e}")
                continue
            like = by_name.get(plan.get("like") or "")
            if like is not None:
                # looks like an existing view: sit beside it, aligned with it
                want = {"rel_box": _rel_box(sec), "near": G.view_page_bbox(like.view), "side": "right"}
            else:
                want = {"rel_box": _rel_box(sec), "near": G.view_page_bbox(base), "side": plan.get("side")}
            pos = place_extra_views(page, [want], keepouts)[0]
            if pos is None:
                res["unplaced"].append(f"section {plan['letter']}: no free sheet space")
                profile = getattr(sec, "CuttingToolWireObject", None)
                doc.removeObject(sec.Name)
                if profile is not None:
                    doc.removeObject(profile.Name)
                doc.recompute()
                continue
            sec.X, sec.Y = pos
            doc.recompute()
            used.add(plan["letter"])
            res["sections"].append(sec.Name)
            res["plans"].append(plan)
    if details:
        plans = decide_details(view_geoms, specs, scale, used, max_details)
        for plan in plans:
            base = by_name[plan["base"]].view
            try:
                det = add_detail(doc, page, base, plan)
            except Exception as e:
                res["notes"].append(f"detail {plan['letter']} failed: {e}")
                continue
            pos = place_extra_views(page, [{"rel_box": _rel_box(det), "near": G.view_page_bbox(base),
                                            "side": None}], keepouts)[0]
            while pos is None:
                # no room: try the next smaller preferred enlargement
                smaller = [x for x in ([1.0] if scale < 1 else []) + DETAIL_SCALES if 1.5 * scale <= x < float(det.Scale) - 1e-9]
                if not smaller:
                    break
                det.Scale = smaller[-1]
                det.Caption = f"{plan['letter']} ({ratio_text(smaller[-1])})"
                plan["scale"], plan["caption"] = smaller[-1], det.Caption
                doc.recompute()
                pos = place_extra_views(page, [{"rel_box": _rel_box(det), "near": G.view_page_bbox(base),
                                                "side": None}], keepouts)[0]
            if pos is None:
                res["unplaced"].append(f"detail {plan['letter']}: no free sheet space")
                doc.removeObject(det.Name)
                doc.recompute()
                continue
            det.X, det.Y = pos
            doc.recompute()
            used.add(plan["letter"])
            res["details"].append(det.Name)
            res["plans"].append(plan)
    return res


# -------------------------------------------------------- frames / records
def _section_plan_of(view):
    raw = getattr(view, SECTION_PROP, "") if SECTION_PROP in view.PropertiesList else ""
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    # hand-made TechDraw section: rebuild from its properties
    plan = {"normal": _l(App.Vector(view.SectionNormal)), "origin": _l(App.Vector(view.SectionOrigin)),
            "half": False, "half_up": None, "axis_point": None, "letter": str(view.SectionSymbol)}
    return plan


def view_center3d(view):
    """Model point shown at the view-local origin (0, 0) of ``view``.

    Regular views: the source bounding-box centre. Sections: the centre of the
    remaining (cut) part, corrected against the projected geometry. Details:
    base centre shifted by the anchor point. Use it to build
    ``dimensions.ViewGeom(view, view_center3d(view), key)``.
    """
    t = view.TypeId
    if t == "TechDraw::DrawViewDetail":
        base = view.BaseView
        bc = view_center3d(base)
        _, bx, bu = _frame(base)
        a = App.Vector(view.AnchorPoint)
        return bc + bx * a.x + bu * a.y
    if t in ("TechDraw::DrawViewSection", "TechDraw::DrawComplexSection"):
        cache = getattr(view_center3d, "_cache", {})
        key = (view.Document.Name, view.Name, tuple(_l(App.Vector(view.SectionOrigin))), tuple(_l(App.Vector(view.SectionNormal))))
        if key in cache:
            return App.Vector(cache[key])
        shape = _source_shape(view)
        if shape is None:
            return App.Vector(0, 0, 0)
        plan = _section_plan_of(view)
        half_up = _v(plan["half_up"]) if plan.get("half_up") else None
        ap = _v(plan["axis_point"]) if plan.get("axis_point") else None
        rest = remaining_shape(shape, App.Vector(view.SectionNormal), App.Vector(view.SectionOrigin), half_up, ap)
        bb = G.exact_bbox(rest)
        c = App.Vector(bb.Center)
        d, x, up = _frame(view)
        lb = G.view_local_bbox(view)
        if lb:
            corners = [App.Vector(px, py, pz) for px in (bb.XMin, bb.XMax) for py in (bb.YMin, bb.YMax) for pz in (bb.ZMin, bb.ZMax)]
            xs = [(p - c).dot(x) for p in corners]
            ys = [(p - c).dot(up) for p in corners]
            # TechDraw centres on the cut shape; any residual offset is measured here
            ddx = 0.5 * ((lb[0] - min(xs)) + (lb[2] - max(xs)))
            ddy = 0.5 * ((lb[1] - min(ys)) + (lb[3] - max(ys)))
            if abs(ddx) < 0.5 * (lb[2] - lb[0]) and abs(ddy) < 0.5 * (lb[3] - lb[1]):
                c = c - x * ddx - up * ddy
        cache[key] = (c.x, c.y, c.z)
        view_center3d._cache = cache
        return c
    return _source_center(view)


def _to_local(p, center, x, up):
    q = App.Vector(p) - center
    return (q.dot(x), q.dot(up))


def _r2(v):
    return round(float(v), 2)


def section_record(view):
    """Snapshot extras of a section view (page mm).

    ``{"section_symbol", "base_view", "half", "normal", "origin",
    "hatch": [[x0, y0, x1, y1, ...] closed loops] (even-odd),
    "cutting_line": {"base_view", "points": [[x, y], ...], "ends": [[x, y], [x, y]],
                     "arrows": [{"tip": [x, y], "dir": [dx, dy]}], "letters": [{"text", "at": [x, y]}]},
    "axis_line": [x0, y0, x1, y1] | None}``
    """
    rec = {"section_symbol": str(getattr(view, "SectionSymbol", "") or "")}
    base = getattr(view, "BaseView", None)
    rec["base_view"] = base.Name if base is not None else None
    plan = _section_plan_of(view)
    rec["half"] = bool(plan.get("half"))
    n = App.Vector(view.SectionNormal)
    if n.Length < 1e-9:
        return rec
    n.normalize()
    O = App.Vector(view.SectionOrigin)
    rec["normal"] = [_r2(c) for c in (n.x, n.y, n.z)]
    rec["origin"] = [_r2(c) for c in (O.x, O.y, O.z)]
    shape = _source_shape(view)
    half_up = _v(plan["half_up"]) if plan.get("half_up") else None
    ap = _v(plan["axis_point"]) if plan.get("axis_point") else None
    # hatch loops on the section view
    loops = []
    if shape is not None:
        try:
            c = view_center3d(view)
            _, x, up = _frame(view)
            sc = G.view_scale(view)
            ox, oy = G.view_origin(view)
            for f in cut_faces(shape, n, O, half_up, ap):
                for w in f.Wires:
                    try:
                        pts = w.discretize(Deflection=0.02)
                    except Exception:
                        pts = [v.Point for v in w.OrderedVertexes]
                    if len(pts) < 3:
                        continue
                    flat = []
                    for p in pts:
                        lx, ly = _to_local(p, c, x, up)
                        flat.extend([_r2(ox + lx * sc), _r2(oy + ly * sc)])
                    loops.append(flat)
            if ap is not None and half_up is not None:
                # dividing line of a half section = the part axis (centre line)
                lb = G.view_local_bbox(view) or [0, 0, 0, 0]
                a = ap + n * (O - ap).dot(n)
                ax_dir = n.cross(half_up)
                lx, ly = _to_local(a, c, x, up)
                tx, ty = ax_dir.dot(x), ax_dir.dot(up)
                if abs(tx) >= abs(ty):
                    p0, p1 = (lb[0], ly), (lb[2], ly)
                else:
                    p0, p1 = (lx, lb[1]), (lx, lb[3])
                rec["axis_line"] = [_r2(ox + p0[0] * sc), _r2(oy + p0[1] * sc), _r2(ox + p1[0] * sc), _r2(oy + p1[1] * sc)]
        except Exception as e:
            rec["hatch_error"] = f"{type(e).__name__}: {e}"
    rec["hatch"] = loops
    if base is not None:
        try:
            rec["cutting_line"] = _cutting_line(view, base, n, O, half_up, ap, rec["section_symbol"])
        except Exception as e:
            rec["cutting_line_error"] = f"{type(e).__name__}: {e}"
    return rec


CUT_OVERSHOOT = 5.0  # mm beyond the base outline
CUT_ARROW = 6.0  # mm, arrow length (tail to tip)


def _cutting_line(view, base, n, O, half_up, ap, letter):
    bc = view_center3d(base)
    _, bx, bu = _frame(base)
    sc = G.view_scale(base)
    ox, oy = G.view_origin(base)
    lb = G.view_local_bbox(base) or [0, 0, 0, 0]
    m = CUT_OVERSHOOT / max(sc, 1e-9)
    box = [lb[0] - m, lb[1] - m, lb[2] + m, lb[3] + m]

    def page(p):
        return [_r2(ox + p[0] * sc), _r2(oy + p[1] * sc)]

    nl = (n.dot(bx), n.dot(bu))  # plane normal in the base view (viewer side)
    L = math.hypot(*nl) or 1.0
    nl = (nl[0] / L, nl[1] / L)
    o = _to_local(O, bc, bx, bu)
    t = (-nl[1], nl[0])  # along the trace

    def clip(p, d):
        """Ray from p along d to the box edge."""
        ts = []
        for k in (0, 1):
            if abs(d[k]) > 1e-12:
                for edge in (box[k], box[k + 2]):
                    ts.append((edge - p[k]) / d[k])
        tpos = min([v for v in ts if v > 1e-9] or [0.0])
        return (p[0] + d[0] * tpos, p[1] + d[1] * tpos)

    if half_up is not None and ap is not None:
        a = _to_local(ap + n * (O - ap).dot(n), bc, bx, bu)
        hu = (half_up.dot(bx), half_up.dot(bu))
        hl = math.hypot(*hu)
        if hl > 1e-6:
            hu = (hu[0] / hl, hu[1] / hl)
            p_start = clip(a, nl)  # from the viewer side ...
            p_end = clip(a, hu)  # ... to the axis, then into the cut half
            pts = [p_start, a, p_end]
        else:
            pts = [clip(o, (-t[0], -t[1])), clip(o, t)]
    else:
        pts = [clip(o, (-t[0], -t[1])), clip(o, t)]
    ends = [pts[0], pts[-1]]
    sight = (-nl[0], -nl[1])  # line of sight: from the viewer side into the part
    arrows, letters = [], []
    end_list = ends if len(pts) == 2 else [pts[-1]]
    for e in end_list:
        tip = page(e)
        tail = (tip[0] - sight[0] * CUT_ARROW, tip[1] - sight[1] * CUT_ARROW)
        arrows.append({"tip": tip, "tail": [_r2(tail[0]), _r2(tail[1])], "dir": [_r2(sight[0]), _r2(sight[1])]})
        # letter beside the arrow tail, outside the outline
        out = (e[0] - o[0], e[1] - o[1]) if len(pts) == 2 else (e[0] - pts[1][0], e[1] - pts[1][1])
        ol = math.hypot(*out) or 1.0
        out = (out[0] / ol, out[1] / ol)
        cands = [(tail[0] - sight[0] * 2.5 + out[0] * 2.5, tail[1] - sight[1] * 2.5 + out[1] * 2.5),
                 (tail[0] - sight[0] * 3.5, tail[1] - sight[1] * 3.5),
                 (tail[0] + out[0] * 4.0, tail[1] + out[1] * 4.0),
                 (tail[0] - sight[0] * 2.5 - out[0] * 3.5, tail[1] - sight[1] * 2.5 - out[1] * 3.5)]
        at = _free_spot(base, cands)
        letters.append({"text": letter, "at": [_r2(at[0]), _r2(at[1])]})
    return {"base_view": base.Name, "points": [page(p) for p in pts], "ends": [page(e) for e in ends],
            "arrows": arrows, "letters": letters}


def detail_record(view):
    """Snapshot extras of a detail view (page mm)."""
    rec = {"reference": str(getattr(view, "Reference", "") or "")}
    base = getattr(view, "BaseView", None)
    rec["base_view"] = base.Name if base is not None else None
    try:
        s = float(view.Scale)
        rec["detail_scale"] = ratio_text(s)
    except Exception:
        pass
    if base is None:
        return rec
    sc = G.view_scale(base)
    ox, oy = G.view_origin(base)
    a = App.Vector(view.AnchorPoint)
    r = float(getattr(view.Radius, "Value", view.Radius)) * sc
    c = (ox + a.x * sc, oy + a.y * sc)
    # letter outside the circle, on the diagonal away from the base view centre
    dx, dy = c[0] - ox, c[1] - oy
    L = math.hypot(dx, dy)
    if L < 1e-6:
        dx, dy, L = 1.0, 1.0, math.sqrt(2)
    dx, dy = dx / L, dy / L
    cands = []
    for k in range(16):
        ang = math.atan2(dy, dx) + (1 if k % 2 else -1) * ((k + 1) // 2) * math.pi / 8
        cands.append((c[0] + math.cos(ang) * (r + 3.5), c[1] + math.sin(ang) * (r + 3.5)))
    at = _free_spot(base, cands)
    vs = float(view.Scale)
    vo = G.view_origin(view)
    rec["boundary"] = {"center": [_r2(vo[0]), _r2(vo[1])], "radius": _r2(float(getattr(view.Radius, "Value", view.Radius)) * vs)}
    rec["detail_circle"] = {"base_view": base.Name, "center": [_r2(c[0]), _r2(c[1])], "radius": _r2(r),
                            "letter_at": [_r2(at[0]), _r2(at[1])]}
    return rec


LETTER_BOX = 3.0  # half size of a label's keep-clear box (5 mm text)


def _letter_obstacles(base):
    """Segments (page mm) a section/detail letter on ``base`` must not touch."""
    segs = []
    try:
        page = base.findParentPage()
    except Exception:
        page = None
    ox, oy = G.view_origin(base)
    sc = G.view_scale(base)
    for _, e in G.iter_edges(base, geom_only=False):
        pts = G.discretize(e, 1.0)
        for p, q in zip(pts, pts[1:]):
            segs.append((ox + p[0] * sc, oy + p[1] * sc, ox + q[0] * sc, oy + q[1] * sc))
    if page is not None:
        from .snapshot import _dimension_record

        for obj in page.Views:
            if obj.TypeId != "TechDraw::DrawViewDimension":
                continue
            try:
                rec = _dimension_record(obj)
            except Exception:
                continue
            for ln in rec.get("lines", []):
                segs.append(tuple(ln))
            tb = rec.get("text_box")
            if tb:
                segs.extend([(tb[0], tb[1], tb[2], tb[1]), (tb[2], tb[1], tb[2], tb[3]), (tb[0], tb[3], tb[2], tb[3]),
                             (tb[0], tb[1], tb[0], tb[3]), (tb[0], tb[1], tb[2], tb[3])])
    return segs


def _free_spot(base, cands):
    """First candidate label position whose box is clear of edges and dimensions."""
    try:
        segs = _letter_obstacles(base)
    except Exception:
        return cands[0]
    best = None
    for i, p in enumerate(cands):
        box = [p[0] - LETTER_BOX, p[1] - LETTER_BOX, p[0] + LETTER_BOX, p[1] + LETTER_BOX]
        hits = sum(1 for sg in segs if G.seg_hits_box(sg, box))
        if hits == 0:
            return p
        if best is None or hits < best[0]:
            best = (hits, p)
    return best[1]
