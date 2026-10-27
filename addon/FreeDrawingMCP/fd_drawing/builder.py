"""One-shot automatic drawing creation.

Pipeline (each step cites the guideline it implements):

1. classify the part (plate / turned / prismatic) and pick the front view that
   shows the most visible and fewest hidden edges [02 C.1]
2. pick the minimum view set that covers the part's features, with additional
   views where needed for feature coverage [02 C.2 C.3]
3. plan dimensions (holes, diameters, baseline coordinates) [04]
4. choose the largest preferred scale whose layout - views plus
   dimension stacks - fits beside the title block, notes and isometric view [00 A.8]
5. place views, dimensions, centre lines [01 B.3], compact isometric 1:2 in
   the bottom-right zone [C.7], general notes and title block [00 05 14]
6. dedupe pass [P.5] and record metadata used by the inspector

Modelled screw threads are drawn by ISO 6410 convention: the helical
geometry is replaced by plain cylinders for the orthographic views (a
``<name>_drawing_geometry`` object), thin cosmetic thread lines / 3/4 circles
are added and the diameter callout becomes the thread designation; the shaded
isometric keeps the real thread.
"""

import math
import os

import FreeCAD as App
import Part

from . import analysis, dimensions, features, importer, page as page_mod, planner, shaded, sheetmetal, templates
from . import progress
from .layout_policy import balanced_stacks, preserves_coverage, retain_parent_dimensions
from . import threads as threads_mod
from . import geometry as G
from .snapshot import set_meta
from .standards import (
    CENTER_LINE_STYLE, DEFAULT_NOTES, DIM_FONT_SIZE, ENLARGEMENT_SCALES, ISO_VIEW_SCALE,
    LINE_THICK, LINE_THIN, REDUCTION_SCALES, SHEET_MARGIN, VIEW_GAP,
)

GENERATOR = "free-drawing-mcp"
NOTE_SIZE = 3.5
RADIAL_MARGIN = 16.0  # paper room for a diameter/radius callout outside a view

# --------------------------------------------------------------------- source
def load_source(doc, source=None):
    """Return ``(part_feature, info)`` to draw.

    ``source``: CAD file path (STEP/IGES/BREP), or an object name/label in
    ``doc``, or None for the largest top-level solid. Rotated parts are drawn
    from an aligned copy so the principal views are true views.
    """
    info = {"warnings": [], "components": [], "aligned": False}
    if source and isinstance(source, str) and os.path.isfile(source):
        shape, warns = importer.read_shape(source)
        info["warnings"].extend(warns)
        info["components"] = importer.components(shape, source)
        name = os.path.splitext(os.path.basename(source))[0]
        obj = doc.addObject("Part::Feature", _safe_name(name))
        obj.Label = name
    else:
        if source:
            obj = doc.getObject(source) or next((o for o in doc.Objects if o.Label == source), None)
            if obj is None:
                raise ValueError(f"no object {source!r} in document {doc.Name!r}")
        else:
            obj = _largest_solid(doc)
        shape = obj.Shape
        if shape.isNull() or not shape.Faces:
            raise ValueError(f"object {obj.Name!r} has no faces to draw")
        info["components"] = importer.components(shape)
    aligned, rot = importer.align(shape)
    if rot is not None:
        info["aligned"] = True
        info["warnings"].append("part is not aligned to X/Y/Z; drawn from a copy aligned to its own principal faces")
        if obj.Shape is not None and not obj.Shape.isNull() and obj.Shape.Faces:
            # keep the user's object untouched, draw an aligned copy
            src = obj
            obj = doc.addObject("Part::Feature", src.Name + "_aligned")
            obj.Label = src.Label + " (aligned)"
    obj.Shape = aligned if rot is not None else shape
    doc.recompute()
    return obj, info


def _largest_solid(doc):
    cands = []
    for o in doc.Objects:
        if not hasattr(o, "Shape") or o.TypeId.startswith("TechDraw"):
            continue
        try:
            vol = o.Shape.Volume if o.Shape.Solids else 0.0
        except Exception:
            vol = 0.0
        if vol <= 0:
            continue
        consumers = [p for p in o.InList if hasattr(p, "Shape") and not p.TypeId.startswith("TechDraw")
                     and p.TypeId not in ("App::DocumentObjectGroup", "App::Part")]
        if consumers:
            continue
        cands.append((vol, o))
    if not cands:
        raise ValueError(f"document {doc.Name!r} has no solid to draw; pass source=")
    return max(cands, key=lambda c: c[0])[1]


def _safe_name(s):
    out = "".join(ch if ch.isalnum() else "_" for ch in s)
    return out if out and not out[0].isdigit() else "Part_" + out


# ------------------------------------------------------------- orientation
_CANDIDATES = [
    App.Vector(0, -1, 0), App.Vector(0, 1, 0), App.Vector(1, 0, 0),
    App.Vector(-1, 0, 0), App.Vector(0, 0, 1), App.Vector(0, 0, -1),
]


def _in_plane_axes(d):
    return [a for a in (App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)) if abs(a.dot(d)) < 1e-9]


def _extent_along(shape, vec):
    bb = shape.BoundBox
    return abs(vec.x) * bb.XLength + abs(vec.y) * bb.YLength + abs(vec.z) * bb.ZLength


def _proj_score(shape, d):
    """Cheap 'shows more than it hides' score: feature area facing the viewer.

    Faces whose normal points at the viewer are seen directly; curved and
    small faces (holes, bosses, pockets) weigh more than large flat ones, so the
    side with the functional features wins ties [C.1].
    """
    score = 0.0
    for f in shape.Faces:
        try:
            u0, u1, v0, v1 = f.ParameterRange
            n = f.normalAt(0.5 * (u0 + u1), 0.5 * (v0 + v1))
        except Exception:
            continue
        facing = n.dot(d)
        weight = 1.0 if type(f.Surface).__name__ == "Plane" else 3.0
        score += weight * facing * (f.Area ** 0.5)
    return score


SUBSETS = [["Front"], ["Front", "Top"], ["Front", "Right"], ["Front", "Top", "Right"]]


def frame_axes(d, x):
    """Model axis each standard view looks along, and the two it shows."""
    up = G.up_vector(d, x)
    an = G.axis_name
    return {"Front": (an(d), {an(x), an(up)}), "Top": (an(up), {an(x), an(d)}), "Right": (an(x), {an(up), an(d)})}


def unmet(axes_of_views, reqs):
    out = []
    for r in reqs:
        for kind, a in planner.needs(r):
            if not any((a in plane) if kind == "in_plane" else (a == along) for along, plane in axes_of_views):
                out.append(r)
                break
    return out


def choose_layout(shape, reqs, turned_axis=None, views="auto"):
    """Pick front direction and the smallest view set showing every requirement.

    Returns ``(direction, x_direction, view_names)``. Thickness requires a
    dimensioned edge view and cannot replace a missing projection with a note.
    """
    fixed = list(views) if views and views != "auto" else None
    best = None
    for d in _CANDIDATES:
        inplane = _in_plane_axes(d)
        x = max(inplane, key=lambda a: _extent_along(shape, a))
        if turned_axis:
            tvec = App.Vector(*[1 if c == turned_axis else 0 for c in "XYZ"])
            if any(abs(abs(a.dot(tvec)) - 1) < 1e-9 for a in inplane):
                x = tvec  # turning axis horizontal [02 good habits]
        fa = frame_axes(d, x)
        front_cov = len(reqs) - len(unmet([fa["Front"]], reqs))
        z_up = 0 if abs(G.up_vector(d, x).z) > 0.99 else 1
        for sub in ([fixed] if fixed else SUBSETS):
            miss = unmet([fa[n] for n in sub if n in fa], reqs)
            # turned parts: the profile (axis in the plane, horizontal) is the front [02]
            turned_pen = 0 if not turned_axis or turned_axis in fa["Front"][1] else 1
            key = (len(miss), len(sub), turned_pen, z_up, -front_cov)
            if best is None or key < best[0]:
                best = (key, [(d, x, sub)])
            elif key == best[0]:
                best[1].append((d, x, sub))
    # ties: the direction with the most visible / fewest hidden edges [C.1]
    ties = best[1]
    if len(ties) > 1:
        scored = []
        for d, x, sub in ties:
            sc = _proj_score(shape, d) * (1.05 if d == _CANDIDATES[0] else 1.0)
            scored.append((sc, (d, x, sub)))
        d, x, sub = max(scored, key=lambda c: c[0])[1]
    else:
        d, x, sub = ties[0]
    return d, x, list(sub)


# Side of the front view each projection sits on, first-angle [02].
SIDE_FIRST = {"Top": "bottom", "Bottom": "top", "Right": "left", "Left": "right"}
OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
EXTRA_VIEWS = ["Top", "Right", "Left", "Bottom"]


def side_of_front(name, projection):
    side = SIDE_FIRST[name]
    return side if projection != "third" else OPPOSITE[side]


def occupied_sides(names, projection):
    occ = {n: set() for n in names}
    for n in names:
        if n == "Front" or n not in SIDE_FIRST:
            continue
        side = side_of_front(n, projection)
        occ["Front"].add(side)
        occ[n].add(OPPOSITE[side])
    return occ


# ------------------------------------------------------------------ layout
def _ext(depths, radial, name, side):
    return dimensions.side_extent(depths.get(name, {}).get(side, 0)) + (RADIAL_MARGIN if side in radial.get(name, ()) else 0.0)


def arrange(boxes, names, depths, radial, projection, s):
    """View centres relative to the front view, and the overall layout box."""
    sb = {n: [v * s for v in boxes[n]] for n in names}
    pos = {"Front": (0.0, 0.0)}
    F = sb["Front"]
    for n in names:
        if n == "Front" or n not in SIDE_FIRST:
            continue
        side = side_of_front(n, projection)
        B = sb[n]
        gap = _ext(depths, radial, "Front", side) + VIEW_GAP + _ext(depths, radial, n, OPPOSITE[side])
        if side == "bottom":
            pos[n] = (0.0, F[1] - gap - B[3])
        elif side == "top":
            pos[n] = (0.0, F[3] + gap - B[1])
        elif side == "left":
            pos[n] = (F[0] - gap - B[2], 0.0)
        else:
            pos[n] = (F[2] + gap - B[0], 0.0)
    boxes_out = []
    for n in names:
        b = sb[n]
        x, y = pos[n]
        boxes_out.append([
            x + b[0] - _ext(depths, radial, n, "left"), y + b[1] - _ext(depths, radial, n, "bottom"),
            x + b[2] + _ext(depths, radial, n, "right"), y + b[3] + _ext(depths, radial, n, "top"),
        ])
    lay = [min(b[0] for b in boxes_out), min(b[1] for b in boxes_out),
           max(b[2] for b in boxes_out), max(b[3] for b in boxes_out)]
    return pos, lay


def _fit(lay, region):
    w, h = lay[2] - lay[0], lay[3] - lay[1]
    rw, rh = region[2] - region[0], region[3] - region[1]
    if w > rw or h > rh:
        return None
    # centre the layout box in the region; return front-view offset
    cx = region[0] + (rw - w) / 2.0 - lay[0]
    cy = region[1] + (rh - h) / 2.0 - lay[1]
    return cx, cy


def _regions(frame, keepouts):
    m = SHEET_MARGIN
    fr = [frame[0] + m, frame[1] + m, frame[2] - m, frame[3] - m]
    ko = [k for k in keepouts if k]
    col_x = min([k[0] for k in ko if k[2] >= fr[2] - 1] + [fr[2]]) - m
    low_top = max([k[3] for k in ko if k[0] <= fr[0] + 1] + [fr[1] - m]) + m
    all_top = max([k[3] for k in ko] + [fr[1] - m]) + m
    mid_top = max([k[3] for k in ko if k[0] <= fr[0] + 1 or k[2] < col_x] + [fr[1] - m]) + m
    right_ko = [k for k in ko if k[2] >= fr[2] - 1]
    beside_x = min([k[0] for k in right_ko if k[1] > min([kk[1] for kk in right_ko] + [fr[1]]) + 1] + [fr[2]]) - m
    regions = [
        [fr[0], low_top, col_x, fr[3]],  # left of the title-block column
        [fr[0], mid_top, beside_x, fr[3]],  # above the title block, left of the isometric view
        [fr[0], all_top, fr[2], fr[3]],  # full-width band above everything
    ]
    return [r for r in regions if r[2] > r[0] and r[3] > r[1]]


def _largest_primary_scale(views, names, specs, projection, scales, frame, keepouts):
    boxes = {name: G.view_local_bbox(views[name]) for name in names}
    radial = {}
    for spec in specs:
        if spec["type"] in ("Diameter", "Radius"):
            radial.setdefault(spec["view"].name, set()).update({"top", "right"})
    for scale in scales:
        for plan in (specs, balanced_stacks(specs)):
            _, bounds = arrange(boxes, names, dimensions.stack_depths(plan), radial, projection, scale)
            if any(_fit(bounds, region) is not None for region in _regions(frame, keepouts)):
                return scale
    return 0


def _reorient_group(group, names, x_direction):
    """Rebuild projection geometry before any dimensions or dependent views exist."""
    doc = group.Document
    for view in list(group.Views):
        if str(view.Type) != "Front":
            group.removeProjection(str(view.Type))
    group.Anchor.XDirection = x_direction
    doc.recompute()
    for name in names:
        if name != "Front":
            group.addProjection(name)
    doc.recompute()
    views = {str(view.Type): view for view in group.Views}
    for view in views.values():
        view.HardHidden = True
        view.SmoothVisible = False
        view.SeamVisible = False
        _style_view(view)
    doc.recompute()
    return views


# ------------------------------------------------------------- centre lines
def add_centerlines(view, shape, scale):
    """Dash-dot centre lines on every cylinder [01 B.3]: a cross on circles,
    an axis line on the side view, 3 mm beyond the outline on paper."""
    d = App.Vector(view.Direction)
    x = App.Vector(view.XDirection)
    up = G.up_vector(d, x)
    ext = 3.0 / max(scale, 1e-6)
    made = 0
    seen = set()
    for c in analysis.cylinders(shape):
        if not c["axis"]:
            continue
        axis = App.Vector(*[1 if a == c["axis"] else 0 for a in "XYZ"])
        center = App.Vector(*c["center"])
        if abs(abs(axis.dot(d)) - 1) < 1e-6:
            if not c["full"]:
                continue
            key = ("cross", round(center.dot(x), 2), round(center.dot(up), 2))
            if key in seen:
                continue
            seen.add(key)
            half = c["radius"] + ext
            for vec in (x, up):
                view.makeCosmeticLine3D(center - vec * half, center + vec * half, CENTER_LINE_STYLE, LINE_THIN / 1.4)
                made += 1
        elif abs(axis.dot(d)) < 1e-6:
            lo, hi = c["extent"]
            idx = "XYZ".index(c["axis"])
            p1 = App.Vector(center); p2 = App.Vector(center)
            p1[idx] = lo - ext
            p2[idx] = hi + ext
            key = ("axis", round(p1.dot(x), 2), round(p1.dot(up), 2), round(p2.dot(x), 2), round(p2.dot(up), 2))
            if key in seen:
                continue
            seen.add(key)
            view.makeCosmeticLine3D(p1, p2, CENTER_LINE_STYLE, LINE_THIN / 1.4)
            made += 1
    return made


# ------------------------------------------------------------- threads
THREAD_LINE_WEIGHT = 0.25  # thin continuous line [ISO 6410 / ISO 128-20 type 01.1]
_AXV = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}


def drawing_geometry(doc, obj):
    """Detect modelled threads on ``obj`` and build the ISO 6410 drawing geometry.

    Returns ``(draw_obj, threads, warnings)``; ``draw_obj`` is ``obj`` itself when
    the part has no (simplifiable) thread.
    """
    try:
        thr = threads_mod.detect_threads(obj.Shape)
    except Exception as e:
        return obj, [], [f"thread detection failed ({type(e).__name__}: {e})"]
    if not thr:
        return obj, [], []
    base = obj.Shape
    simp = threads_mod.simplify(base, thr)
    warns = [f"thread {t['designation']} could not be simplified ({t.get('simplify_error')}); drawn as modelled"
             for t in thr if not t.get("simplified")]
    thr = [t for t in thr if t.get("simplified")]
    if simp is base or not thr:
        return obj, [], warns
    dobj = doc.addObject("Part::Feature", obj.Name + "_drawing_geometry")
    dobj.Label = obj.Label + " (drawing geometry)"
    dobj.Shape = simp
    try:
        dobj.Visibility = False
    except Exception:
        pass
    doc.recompute()
    return dobj, thr, warns


def _thread_end_edge(shape, t, coord, r):
    """A circular outline edge of radius >= r at axial ``coord`` (shoulder / end face)."""
    i = "XYZ".index(t["axis"])
    c = t["center"]
    for e in shape.Edges:
        cu = e.Curve
        if type(cu).__name__ != "Circle" or cu.Radius < r - 0.01:
            continue
        cc = cu.Center
        p = (cc.x, cc.y, cc.z)
        if abs(p[i] - coord) < 1e-3 and all(abs(p[k] - c[k]) < 1e-3 for k in range(3) if k != i):
            return True
    return False


def _end_seen(shape, t, coord, sign):
    """Is the thread end at ``coord`` visible from the ``sign`` side along the axis?"""
    i = "XYZ".index(t["axis"])
    r = 0.25 * (t["minor_dia"] + t["major_dia"])
    bb = G.exact_bbox(shape)
    stop = getattr(bb, t["axis"] + ("Max" if sign > 0 else "Min")) + sign * 0.5
    n = 24
    for k in range(n + 1):
        x = coord + sign * 0.2 + (stop - coord - sign * 0.2) * k / n
        for ang in (0.4, 2.0, 3.6, 5.2):
            p = list(t["center"])
            p[i] = x
            j1, j2 = [m for m in range(3) if m != i]
            p[j1] += r * math.cos(ang)
            p[j2] += r * math.sin(ang)
            try:
                if shape.isInside(App.Vector(*p), 1e-4, True):
                    return False
            except Exception:
                return True
    return True


def add_thread_lines(view, shape, thr, arc_start=100.0, arc_end=370.0):
    """ISO 6410 thread representation on one view (``shape`` = drawing geometry).

    Axis in the plane: thin lines at the minor (external) / major (internal)
    diameter over the thread length, and a thread-limit line where no outline
    edge marks the end. Axis along the view: thin 3/4 circle at the same
    diameter when the thread end faces the viewer. Internal threads in an
    exterior view are hidden, so their lines are dashed.
    """
    d = App.Vector(view.Direction)
    if d.Length < 1e-9:
        return 0
    d.normalize()
    made = 0
    for t in thr:
        a = _AXV[t["axis"]]
        i = "XYZ".index(t["axis"])
        lo, hi = t["extent"]
        c_lo = App.Vector(*t["center"])
        c_hi = App.Vector(*t["center"])
        c_lo[i], c_hi[i] = lo, hi
        r_major = (t["nominal"] or t["major_dia"]) / 2.0
        r_thin = r_major if t["internal"] else t["minor_dia"] / 2.0
        hidden = bool(t["internal"])
        dot = a.dot(d)
        try:
            if abs(dot) < 1e-6:
                perp = d.cross(a)
                perp.normalize()
                for sg in (1, -1):
                    view.makeCosmeticLine3D(c_lo + perp * (sg * r_thin), c_hi + perp * (sg * r_thin),
                                            2 if hidden else 1, THREAD_LINE_WEIGHT)
                    made += 1
                for end, coord, is_open in ((c_lo, lo, t["open"][0]), (c_hi, hi, t["open"][1])):
                    if is_open or _thread_end_edge(shape, t, coord, r_major):
                        continue
                    # thread limit: across the major diameter (thick if visible) [ISO 6410]
                    view.makeCosmeticLine3D(end + perp * r_major, end - perp * r_major,
                                            2 if hidden else 1, THREAD_LINE_WEIGHT if hidden else LINE_THICK)
                    made += 1
            elif abs(abs(dot) - 1) < 1e-6:
                sign = 1 if dot > 0 else -1
                end, coord = (c_hi, hi) if sign > 0 else (c_lo, lo)
                if _end_seen(shape, t, coord, sign):
                    view.makeCosmeticCircleArc3d(end, r_thin, arc_start, arc_end, 1, THREAD_LINE_WEIGHT)
                    made += 1
        except Exception:
            continue
    return made


def tapdrill_review(reqs):
    """Plain holes whose diameter is an ISO tap drill: maybe a thread that is not modelled."""
    out = []
    for r in reqs:
        if r.get("kind") != "dia" or r.get("feature") != "hole":
            continue
        m = threads_mod.tap_drill_matches(r["dia"])
        if m:
            names = " / ".join(x["thread"] for x in m)
            out.append({"dia": round(float(r["dia"]), 3), "count": r.get("count", 1), "matches": m,
                        "note": f"⌀{threads_mod._fmt(r['dia'])} is the ISO tap drill for {names}: "
                                "if the hole is tapped, add the thread callout"})
    return out


def _style_view(view, hidden=True):
    vo = getattr(view, "ViewObject", None)
    if vo is None:
        return
    for prop, val in (("LineWidth", LINE_THICK), ("HiddenWidth", LINE_THIN), ("IsoWidth", LINE_THIN),
                      ("ExtraWidth", LINE_THIN), ("ArcCenterMarks", False)):
        try:
            setattr(vo, prop, val)
        except Exception:
            pass


def _style_dim(dim):
    vo = getattr(dim, "ViewObject", None)
    if vo is None:
        return
    # Paper-space sizes remain readable when the model scale changes.
    # 3.5 mm text above the line; set per object, user preferences untouched.
    for prop, val in (("LineWidth", LINE_THIN), ("Fontsize", DIM_FONT_SIZE), ("Arrowsize", 3.0),
                      ("Font", "osifont"), ("Color", (0.0, 0.0, 0.0)), ("StandardAndStyle", "ISO Oriented")):
        try:
            setattr(vo, prop, val)
        except Exception:
            pass


def add_note(doc, page, lines, x, y, name="Notes", size=NOTE_SIZE):
    note = doc.addObject("TechDraw::DrawViewAnnotation", name)
    page.addView(note)
    note.Text = list(lines)
    note.TextSize = size
    note.X, note.Y = x, y
    return note


def note_size(lines, size=NOTE_SIZE):
    w = max([0.6 * size * len(s) for s in lines] + [1.0])
    h = len(lines) * size * 1.5
    return w, h


def iso_frame(front_dir):
    """Isometric from front-right-top with model Z up, so parts sit naturally."""
    z = App.Vector(0, 0, 1)
    h = App.Vector(front_dir.x, front_dir.y, 0)
    if h.Length < 1e-9:
        h = App.Vector(0, -1, 0)
    h.normalize()
    r = z.cross(h)
    idir = h + r + z
    idir.normalize()
    ix = z.cross(idir)
    ix.normalize()
    return idir, ix


PARTS_LIST_SIZE = 3.0


def parts_list_lines(comps):
    """Parts list read upwards: header row at the bottom, next to the title block [12]."""
    rows = [f"{c['item']:>3} | {c['qty']:>3} | {c['name']}" for c in comps]
    return list(reversed(rows)) + ["ITEM | QTY | DESIGNATION"]


def add_balloons(doc, page, view, shape, comps, center3d):
    """One balloon per parts-list item, pointing at the first solid of the item [12]."""
    d = App.Vector(view.Direction)
    x = App.Vector(view.XDirection)
    up = G.up_vector(d, x)
    sc = G.view_scale(view)
    lb = G.view_local_bbox(view) or [0, 0, 0, 0]
    made = []
    for n, c in enumerate(comps):
        solid = shape.Solids[c["solids"][0]]
        p = solid.BoundBox.Center - center3d
        lx, ly = p.dot(x) * sc, p.dot(up) * sc
        try:
            b = doc.addObject("TechDraw::DrawViewBalloon", "Balloon")
            b.SourceView = view
            b.OriginX, b.OriginY = lx, ly
            b.X = lb[2] * sc + 14.0
            b.Y = lb[3] * sc - n * 12.0
            b.Text = str(c["item"])
            page.addView(b)
            made.append(b)
        except Exception:
            continue
    return made


# --------------------------------------------------------------- top level
def create_drawing(doc, source=None, template="A3", orientation="landscape", projection="first",
                   scale="auto", views="auto", iso=True, title=None, title_block=None, notes=None,
                   auto_dimension=True, centerlines=True, page_name="Page", allow_enlarge=True,
                   iso_style="shaded", iso_color=None, purpose="manufacturing", sections=True, details=True,
                   dimensioned_only=True, section_cut_only=False):
    projection = "third" if str(projection).lower().startswith("third") else "first"
    progress.reset()
    progress.stage("import", source=str(source))
    obj, src_info = load_source(doc, source)
    progress.stage("analyse", faces=len(obj.Shape.Faces), solids=len(obj.Shape.Solids))
    warnings = list(src_info["warnings"])
    # modelled threads: orthographic views use the ISO 6410 drawing geometry
    draw_obj, thr, thr_warn = drawing_geometry(doc, obj) if auto_dimension or centerlines else (obj, [], [])
    warnings.extend(thr_warn)
    shape = draw_obj.Shape
    info = analysis.classify(shape)
    coverage_gaps = []
    if any(type(f.Surface).__name__ == "Cylinder" and G.axis_name(f.Surface.Axis) is None for f in shape.Faces):
        coverage_gaps.append("inclined cylindrical feature: requires an auxiliary view and explicit diameter/location review")
    if any(type(f.Surface).__name__ in ("BSplineSurface", "BezierSurface", "SurfaceOfExtrusion", "SurfaceOfRevolution") for f in shape.Faces):
        coverage_gaps.append("freeform surface: requires an authoritative model/profile specification and functional sections")
    warnings.extend(coverage_gaps)
    if len(shape.Solids) > 1:
        info["assembly"] = True

    page, tpl, tpl_path = page_mod.create_page(doc, page_name, template, orientation)
    doc.recompute()
    sheet = templates.parse_template(tpl_path, float(page.PageWidth), float(page.PageHeight))
    if sheet["projection_symbol"] and sheet["projection_symbol"] != projection:
        warnings.append(f"template shows the {sheet['projection_symbol']}-angle symbol but views use {projection}-angle")

    turned_axis = info.get("axis") if info["kind"] == "turned" else None
    reqs, _ = (features.requirements(shape, turned_axis, assembly=bool(info.get("assembly")), threads=thr)
               if auto_dimension else ([], {}))
    tap_review = tapdrill_review(reqs)
    # Bent sheet metal: one thickness, outside faces, inside bend radii [15]
    sheet_info = sheetmetal.detect(shape) if auto_dimension and not info.get("assembly") else None
    if sheet_info:
        reqs, _ = sheetmetal.filter_requirements(reqs, shape, sheet_info)
        info["kind"] = "sheet"
    progress.stage("choose_views", requirements=len(reqs))
    front_dir, front_x, names = choose_layout(shape, reqs, turned_axis, views)
    thickness = sheet_info["t"] if sheet_info else None
    grp = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
    page.addView(grp)
    grp.Source = [draw_obj]
    grp.ProjectionType = "First angle" if projection == "first" else "Third angle"
    grp.ScaleType = "Custom"
    grp.Scale = 1.0
    grp.AutoDistribute = False
    progress.stage("projection", view="Front", direction=[round(c, 4) for c in front_dir])
    anchor = grp.addProjection("Front")
    doc.recompute()
    anchor = grp.Anchor if getattr(grp, "Anchor", None) is not None else anchor
    anchor.Direction = front_dir
    anchor.XDirection = front_x
    for n in names:
        if n != "Front":
            progress.stage("projection", view=n)
            grp.addProjection(n)
    doc.recompute()
    vobjs = {str(v.Type): v for v in grp.Views}
    for v in vobjs.values():
        v.HardHidden = True  # hidden lines visible on orthographic views [C.6]
        v.SmoothVisible = False  # tangent edges are not drawn on ISO orthographic views
        v.SeamVisible = False
        _style_view(v)
    doc.recompute()

    center3d = G.exact_bbox(shape).Center

    def _plan():
        occ_ = occupied_sides(names, projection)
        geoms_ = [dimensions.ViewGeom(vobjs[n], center3d, n) for n in names]
        specs_, unplaced_ = planner.realize(reqs, geoms_, occ_)
        return geoms_, specs_, unplaced_

    progress.stage("plan_dimensions", views=list(names))
    geoms, specs, unplaced = _plan()
    if auto_dimension and (views == "auto" or not views):
        # Completeness first: add views until every requirement is placed and
        # as few as possible sit on hidden lines [E.1][E.5]; then drop views
        # that carry nothing [C.3].
        def badness(sp, up):
            return (len(up), sum(1 for x in sp if x.get("hidden")))

        for extra in EXTRA_VIEWS:
            if extra in names or not badness(specs, unplaced)[0] and not badness(specs, unplaced)[1]:
                continue
            if len(names) >= 4:
                break
            before = badness(specs, unplaced)
            progress.stage("projection", view=extra, reason="augment")
            grp.addProjection(extra)
            doc.recompute()
            v_new = next(v for v in grp.Views if str(v.Type) == extra)
            v_new.HardHidden = True
            v_new.SmoothVisible = False
            v_new.SeamVisible = False
            _style_view(v_new)
            names.append(extra)
            vobjs[extra] = v_new
            doc.recompute()
            g2, s2, u2 = _plan()
            if badness(s2, u2) < before:
                geoms, specs, unplaced = g2, s2, u2
                warnings.append(f"added {extra} view to dimension features completely")
            else:
                grp.removeProjection(extra)
                names.remove(extra)
                vobjs.pop(extra, None)
                doc.recompute()
        used = {sp["view"].name for sp in specs}
        redundant = [n for n in names if n != "Front" and n not in used]
        if redundant:
            for n in redundant:
                grp.removeProjection(n)
                names.remove(n)
                vobjs.pop(n, None)
            doc.recompute()
            geoms, specs, unplaced = _plan()
            warnings.append(f"dropped redundant view(s) {redundant}: they would carry no unique dimension")
    if unplaced:
        warnings.append(f"{len(unplaced)} requirement(s) could not be dimensioned automatically: " + "; ".join(unplaced[:6]))
    boxes = {n: (G.view_local_bbox(vobjs[n]) or [0, 0, 0, 0]) for n in names}
    depths = dimensions.stack_depths(specs)
    radial = {}
    for s in specs:
        if s["type"] in ("Diameter", "Radius"):
            radial.setdefault(s["view"].name, set()).update({"top", "right"})

    # notes & keep-outs ---------------------------------------------------
    note_lines = list(DEFAULT_NOTES if notes is None else notes)
    if title_block and title_block.get("general_tolerances"):
        note_lines = [("GENERAL TOLERANCES: " + title_block["general_tolerances"]) if l.startswith("GENERAL TOLERANCES") else l for l in note_lines]
    frame = sheet["frame"]
    tb = sheet["title_block"] or [frame[2], frame[1], frame[2], frame[1]]
    nw, nh = note_size(note_lines) if note_lines else (0, 0)
    notes_box = [frame[0] + SHEET_MARGIN, frame[1] + SHEET_MARGIN, frame[0] + SHEET_MARGIN + nw, frame[1] + SHEET_MARGIN + nh] if note_lines else None
    if notes_box and notes_box[2] > tb[0] - SHEET_MARGIN:
        warnings.append("general notes are wider than the space left of the title block")

    comps = src_info["components"] if info.get("assembly") else []
    pl_lines = parts_list_lines(comps) if comps else []
    pl_box = None
    if pl_lines:
        pw, ph = note_size(pl_lines, PARTS_LIST_SIZE)
        pl_box = [tb[0], tb[3] + SHEET_MARGIN / 2, tb[0] + pw, tb[3] + SHEET_MARGIN / 2 + ph]

    if auto_dimension and dimensioned_only and views == "auto" and not turned_axis and scale == "auto":
        trial_scales = (list(reversed(ENLARGEMENT_SCALES)) if allow_enlarge else []) + REDUCTION_SCALES
        keep = [G.expand(box, 2.0) for box in (tb, notes_box, pl_box) if box]
        original_names, original_specs = list(names), specs
        original_bad = sum(bool(spec.get("hidden")) for spec in specs)
        original_scale = _largest_primary_scale(vobjs, names, specs, projection, trial_scales, frame, keep)
        rotated_x = G.up_vector(front_dir, front_x)
        rotate_names = {"Front": "Front", "Top": "Right", "Bottom": "Left", "Right": "Bottom", "Left": "Top"}
        names = [rotate_names.get(name, name) for name in names]
        vobjs = _reorient_group(grp, names, rotated_x)
        geoms, specs, unplaced = _plan()
        rotated_scale = _largest_primary_scale(vobjs, names, specs, projection, trial_scales, frame, keep)
        if (rotated_scale > original_scale and preserves_coverage(original_specs, specs)
                and sum(bool(spec.get("hidden")) for spec in specs) <= original_bad):
            front_x = rotated_x
            warnings.append("rotated the standard view group 90 degrees to fit a larger shared scale")
        else:
            names = original_names
            vobjs = _reorient_group(grp, names, front_x)
            geoms, specs, unplaced = _plan()
        boxes = {n: G.view_local_bbox(vobjs[n]) for n in names}
        depths = dimensions.stack_depths(specs)
        radial = {}
        for spec in specs:
            if spec["type"] in ("Diameter", "Radius"):
                radial.setdefault(spec["view"].name, set()).update({"top", "right"})

    iso_view, iso_local = None, None
    if iso:
        iso_view = doc.addObject("TechDraw::DrawViewPart", "Isometric")
        page.addView(iso_view)
        iso_view.Source = [draw_obj]  # line fallback / framing; the shaded image shows the real part
        idir, ix = iso_frame(front_dir)
        iso_view.Direction = idir
        iso_view.XDirection = ix
        iso_view.HardHidden = False  # no hidden lines on the pictorial view [02]
        iso_view.SmoothVisible = False
        iso_view.ScaleType = "Custom"
        iso_view.Scale = ISO_VIEW_SCALE
        iso_view.Caption = ""
        _style_view(iso_view)
        doc.recompute()
        iso_local = G.view_local_bbox(iso_view) or [0, 0, 0, 0]

    progress.stage("layout")
    # scale search ----------------------------------------------------------
    if scale == "auto" or scale is None:
        cands = (list(reversed(ENLARGEMENT_SCALES)) if allow_enlarge else []) + REDUCTION_SCALES
    else:
        cands = [float(_parse_scale(scale))]
    def iso_fit(s):
        """Keep the coloured recognition model legible in its bottom-right zone."""
        if iso_view is None:
            return None, None
        col_w = frame[2] - tb[0]
        max_h = 0.42 * (frame[3] - tb[3])
        longest=max(iso_local[2]-iso_local[0],iso_local[3]-iso_local[1])
        preferred=ISO_VIEW_SCALE
        if longest*preferred<35:
            preferred=next((x for x in [1.0]+ENLARGEMENT_SCALES if longest*x>=35),ENLARGEMENT_SCALES[-1])
        available=list(reversed(ENLARGEMENT_SCALES))+REDUCTION_SCALES
        options = [x for x in available if x <= preferred + 1e-9 and x <= s + 1e-9] or [REDUCTION_SCALES[-1]]
        for iso_s in options:
            w = (iso_local[2] - iso_local[0]) * iso_s + 2.0
            h = (iso_local[3] - iso_local[1]) * iso_s * 1.06 + 3.5  # + ground shadow room
            if w <= col_w - 2 * SHEET_MARGIN and h <= max_h:
                break
        x1 = frame[2] - SHEET_MARGIN
        y0 = tb[3] + SHEET_MARGIN
        if pl_box and x1 - w < pl_box[2] + SHEET_MARGIN:
            y0 = pl_box[3] + SHEET_MARGIN
        return [x1 - w, y0, x1, y0 + h], iso_s

    chosen = None
    layouts = [specs, balanced_stacks(specs)]
    layout_attempts = []
    for s in cands:
        iso_box, iso_s = iso_fit(s)
        keep = [G.expand(tb, 2.0), G.expand(iso_box, 3.0) if iso_box else None, G.expand(notes_box, 2.0) if notes_box else None,
                G.expand(pl_box, 2.0) if pl_box else None]
        fits = []
        for layout_index, candidate_specs in enumerate(layouts):
            pos, lay = arrange(boxes, names, dimensions.stack_depths(candidate_specs), radial, projection, s)
            layout_attempts.append({"scale": s, "balanced": bool(layout_index),
                                    "width": round(lay[2] - lay[0], 2), "height": round(lay[3] - lay[1], 2),
                                    "fits": False})
            for region in _regions(frame, keep):
                off = _fit(lay, region)
                if off is not None:
                    layout_attempts[-1]["fits"] = True
                    slack = min(region[2] - region[0] - (lay[2] - lay[0]), region[3] - region[1] - (lay[3] - lay[1]))
                    fits.append((slack, off, pos, candidate_specs))
        if fits:
            _, off, pos, specs = max(fits, key=lambda f: f[0])
            chosen = (s, pos, off, iso_box, iso_s)
        if chosen:
            break
    if chosen is None:
        s = cands[-1]
        pos, lay = arrange(boxes, names, depths, radial, projection, s)
        off = ((frame[0] + frame[2]) / 2 - (lay[0] + lay[2]) / 2, (frame[1] + frame[3]) / 2 - (lay[1] + lay[3]) / 2)
        chosen = (s, pos, off) + iso_fit(s)
        warnings.append("layout does not fit the sheet at any preferred scale; use a larger sheet (template='A2')")
    s, pos, off, iso_box, iso_s = chosen

    # apply -----------------------------------------------------------------
    grp.Scale = s
    grp.X, grp.Y = off[0] + pos["Front"][0], off[1] + pos["Front"][1]
    for n in names:
        v = vobjs[n]
        v.X, v.Y = pos[n][0] - pos["Front"][0], pos[n][1] - pos["Front"][1]
    if iso_view is not None:
        iso_view.Scale = iso_s
        iso_view.X = iso_box[2] - 1.0 - iso_local[2] * iso_s
        iso_view.Y = iso_box[1] + 1.0 - iso_local[1] * iso_s
    doc.recompute()
    if iso_view is not None and iso_style == "shaded":
        # full-colour shaded isometric, always bottom-right [C.7, project rule]
        try:
            center = ((iso_box[0] + iso_box[2]) / 2.0, (iso_box[1] + iso_box[3]) / 2.0)
            color = tuple(iso_color) if iso_color else shaded.part_color(obj)
            progress.stage("iso_render")
            img, _, _ = shaded.place_shaded_iso(doc, page, obj.Shape, iso_view, iso_s, center, color)
            doc.removeObject(iso_view.Name)
            iso_view = img
        except Exception as e:
            warnings.append(f"shaded isometric failed ({type(e).__name__}: {e}); line isometric kept")
    if pl_lines:
        add_note(doc, page, pl_lines, (pl_box[0] + pl_box[2]) / 2, (pl_box[1] + pl_box[3]) / 2,
                 name="PartsList", size=PARTS_LIST_SIZE)
    doc.recompute()

    n_thread_lines = 0
    if thr:
        for n in names:
            n_thread_lines += add_thread_lines(vobjs[n], shape, thr)
        doc.recompute()
    for g in geoms:
        g.scale = G.view_scale(g.view)
    progress.stage("place_dimensions", count=len(specs))
    created = dimensions.place(specs, page, doc) if auto_dimension else []
    for d in created:
        _style_dim(d)
        if str(getattr(d, "FDRole", "")).startswith("diameter:thread"):
            d.Arbitrary = True  # the callout replaces the measured value (M20×2.5-6g)
    n_center = 0
    if centerlines:
        for n in names:
            n_center += add_centerlines(vobjs[n], shape, s)
    if comps:
        add_balloons(doc, page, vobjs["Front"], shape, comps, center3d)
    note_objs = []
    if note_lines:
        note_objs.append(add_note(doc, page, note_lines, (notes_box[0] + notes_box[2]) / 2, (notes_box[1] + notes_box[3]) / 2))
    doc.recompute()

    fields = page_mod.default_title_fields(doc, s, title or obj.Label)
    if title_block:
        fields.update(title_block)
    page_mod.set_title_block(page, fields)
    from . import sections as section_views

    progress.stage("sections_details")
    # Supplementary views must solve a specific readability/visibility problem.
    # A counterbore already defined by a visible callout does not need another cut.
    minimal_extras = bool(dimensioned_only)
    need_section = any(spec.get("hidden") for spec in specs) or bool(unplaced)
    extra = section_views.add_sections_and_details(
        doc, page, shape, geoms, specs, reqs, info, s,
        keepouts=[box for box in (iso_box, notes_box, pl_box) if box],
        projection=projection, sections=sections and (need_section or not minimal_extras),
        details=details, max_sections=1 if minimal_extras else 2, max_details=1 if minimal_extras else 3,
        cut_only=section_cut_only, small_details_only=minimal_extras)
    warnings.extend(extra["notes"])
    if extra["unplaced"]:
        warnings.append("supplementary views need a larger sheet: " + "; ".join(extra["unplaced"]))
    if auto_dimension and (extra["sections"] or extra["details"]):
        extra_geoms = []
        for name in extra["sections"] + extra["details"]:
            view = doc.getObject(name)
            extra_geoms.append(dimensions.ViewGeom(view, section_views.view_center3d(view), name))
        revised, remaining = planner.realize(reqs, geoms + extra_geoms, occupied_sides(names, projection))
        # Replace the whole plan only when coverage does not regress. References
        # are resolved again on the cut/cropped geometry, never copied by index.
        if preserves_coverage(specs, revised):
            revised = retain_parent_dimensions(specs, revised)
            revised = balanced_stacks(revised)
            for dim in created:
                doc.removeObject(dim.Name)
            doc.recompute()
            created = dimensions.place(revised, page, doc)
            for dim in created:
                _style_dim(dim)
                if str(getattr(dim, "FDRole", "")).startswith("diameter:thread"):
                    dim.Arbitrary = True
            unplaced = remaining
            specs = revised
        else:
            warnings.append("supplementary dimension plan rejected: it would lose an already placed requirement")
    doc.recompute()  # measured values must be current before duplicate detection
    final_layout = None
    if auto_dimension and dimensioned_only and (scale == "auto" or scale is None) and specs:
        from .reflow import maximize_scale
        created, s, final_layout = maximize_scale(
            page, grp, names, vobjs, specs, created, s, cands, arrange, projection,
            [box for box in (iso_box, pl_box) if box])
        from .snapshot import _annotation_record
        for note in note_objs:
            if note.Name.startswith("Notes"):
                notes_box = _annotation_record(note)["bbox"]
        for dim in created:
            _style_dim(dim)
            if str(getattr(dim, "FDRole", "")).startswith("diameter:thread"):
                dim.Arbitrary = True
        page_mod.set_title_block(page, {"scale": page_mod.scale_text(s)})
        if not final_layout["applied"]:
            warnings.append("complete view and dimension footprints do not fit; review layout or use a larger sheet")
    removed = dimensions.dedupe(page, doc)
    doc.recompute()
    visibility_policy = retain_dimensioned_views(page, [iso_view] if iso_view is not None else []) if dimensioned_only else None
    from .annotation_layout import clear_collisions
    dimension_views = {spec["view"].view.Name: spec["view"].view for spec in specs}
    clearance = clear_collisions(page, [dim for dim in page.Views if dim.TypeId == "TechDraw::DrawViewDimension"],
                                 [v for v in dimension_views.values() if bool(v.Visibility)],
                                 bounds=frame, keepouts=[b for b in (tb, iso_box, notes_box, pl_box) if b]) if auto_dimension else None
    if clearance and clearance["unresolved"]:
        warnings.append("annotation collisions remain: " + ", ".join(clearance["unresolved"]))
    visible_names = [name for name in names if bool(vobjs[name].Visibility)]
    visible_iso = iso_view if iso_view is not None and bool(iso_view.Visibility) else None
    visible_extra = dict(extra)
    for kind in ("sections", "details"):
        visible_extra[kind] = [name for name in extra[kind] if bool(doc.getObject(name).Visibility)]
    set_meta(page, generator=GENERATOR, dedupe_ran=True, purpose=purpose, source=obj.Name, part_kind=info["kind"],
             projection=projection, views=visible_names, iso=visible_iso.Name if visible_iso else None,
             notes=[n.Name for n in note_objs], assembly=bool(info.get("assembly")),
             thickness=thickness, unplaced=unplaced,
             sheet=({"t": sheet_info["t"], "inside_radii": sheet_info["inner_radii"],
                     "bends": len(sheet_info["bends"])} if sheet_info else None),
             components=[{k: c[k] for k in ("item", "qty", "name")} for c in src_info["components"]],
             iso_shaded=bool(visible_iso is not None and visible_iso.TypeId == "TechDraw::DrawViewImage"),
             iso_readability_enlarged=bool(visible_iso is not None and iso_s>ISO_VIEW_SCALE),
             threads=[threads_mod.summary(t) for t in thr], tapdrill_review=tap_review,
             drawing_geometry=draw_obj.Name if draw_obj is not obj else None,
             supplementary_views=visible_extra, standards_profile="SN EN ISO / Swiss TPD-GPS",
             dimensioned_only=bool(dimensioned_only), view_visibility_policy=visibility_policy,
             layout_attempts=layout_attempts,
             final_layout=final_layout,
             annotation_clearance=clearance,
             coverage_gaps=coverage_gaps,
             standards_review_required=True)
    doc.recompute()
    timings = progress.finish()
    return {
        "document": doc.Name,
        "page": page.Name,
        "source": obj.Name,
        "part_kind": info["kind"],
        "template": os.path.basename(tpl_path),
        "projection": projection,
        "scale": page_mod.scale_text(s),
        "views": {n: vobjs[n].Name for n in visible_names},
        "isometric": visible_iso.Name if visible_iso else None,
        "iso_scale": page_mod.scale_text(iso_s) if visible_iso else None,
        "dimensions": len(created) - len(removed),
        "centerlines": n_center,
        "threads": [threads_mod.callout(t) for t in thr],
        "thread_lines": n_thread_lines,
        "sections": visible_extra["sections"],
        "details": visible_extra["details"],
        "dimensioned_only": bool(dimensioned_only),
        "view_visibility_policy": visibility_policy,
        "duplicates_removed": removed,
        "unplaced": unplaced,
        "timings": timings,
        "warnings": warnings,
    }


def retain_dimensioned_views(page, recognition_views=()):
    """Hide views without attached dimensions while retaining dependency objects.

    Projection anchors and section/detail parents must remain in the document:
    deleting them can invalidate the dimensions on their dependent views.
    Native Visibility survives saving and also controls the TechDraw GUI sheet.
    The designated isometric recognition view is retained without dimensions.
    """
    children = []
    for obj in page.Views:
        children.append(obj)
        if obj.TypeId == "TechDraw::DrawProjGroup":
            children.extend(obj.Views)
    attached = {}
    for obj in children:
        if obj.TypeId != "TechDraw::DrawViewDimension" or not bool(obj.Visibility):
            continue
        for reference in obj.References2D:
            view = reference[0]
            if view is not None:
                attached.setdefault(view.Name, set()).add(obj.Name)
    hidden, retained = [], []
    recognition = {view.Name for view in recognition_views}
    for view in children:
        if view.TypeId == "TechDraw::DrawProjGroup":
            continue
        if not G.is_type(view, "TechDraw::DrawViewPart", "TechDraw::DrawViewImage"):
            continue
        dimension_names = sorted(attached.get(view.Name, []))
        view.Visibility = bool(dimension_names) or view.Name in recognition
        if dimension_names or view.Name in recognition:
            retained.append({"name": view.Name, "dimensions": dimension_names,
                             "recognition": view.Name in recognition})
        else:
            dependents = [obj.Name for obj in children
                          if getattr(getattr(obj, "BaseView", None), "Name", None) == view.Name]
            hidden.append({"name": view.Name, "type_id": view.TypeId, "reason": "no attached dimension",
                           "retained_in_document": True, "dependent_views": dependents})
    return {"enabled": True, "hidden": hidden, "retained": retained, "removed": []}


def _parse_scale(scale):
    if isinstance(scale, (int, float)):
        return float(scale)
    s = str(scale).replace(" ", "")
    if ":" in s:
        a, b = s.split(":")
        return float(a) / float(b)
    return float(s)
