"""View geometry indexing and dimension placement.

What to dimension is decided in ``features.py`` (3D requirements) and
``planner.py`` (which view, which vertices). This module indexes a view's 2D
geometry (:class:`ViewGeom`) and places the planned dimensions:

* baseline stacks outside the view, first line 10 mm off the outline, 8 mm
  between lines, shortest closest so extension lines never cross text [E.10];
* text wider than its span moves outside the far extension line;
* radial callouts on a free diagonal beyond any stack.
"""

import math
import re

import FreeCAD as App

from . import geometry as G
from .standards import DIAMETER_SYMBOL, DIM_FIRST_OFFSET, DIM_FONT_SIZE, DIM_STEP

ROLE_PROP = "FDRole"


class ViewGeom:
    """Indexed geometry of one DrawViewPart in view-local (unscaled, Y-up) frame.

    ``key`` is the view's role ('Front', 'Top', ...) used for layout decisions.
    """

    def __init__(self, view, center3d, key=None):
        self.view = view
        self.name = key or str(getattr(view, "Type", "") or view.Name)
        self.scale = G.view_scale(view)
        self.vis = G.visibility_map(view)
        self.edges = list(G.iter_edges(view))
        self.vertices = list(G.iter_vertices(view))
        self.direction = App.Vector(view.Direction)
        self.xdir = App.Vector(view.XDirection)
        if self.xdir.Length < 1e-9:
            self.xdir = App.Vector(1, 0, 0)
        self.up = G.up_vector(self.direction, self.xdir)
        self.center3d = center3d
        self.lines = []  # (index, p0, p1, visible)
        arcs = {}
        for i, e in self.edges:
            curve = type(e.Curve).__name__
            visible = self.vis.get(i, True)
            if curve == "Circle":
                c = e.Curve.Center
                closed = len(e.Vertexes) == 1 or e.isClosed()
                iv = (0.0, 2 * math.pi) if closed else G.arc_interval(e)
                k = (round(c.x, 3), round(c.y, 3), round(e.Curve.Radius, 3))
                rec = arcs.setdefault(k, {"edge": i, "center": (float(c.x), float(c.y)),
                                          "radius": float(e.Curve.Radius), "visible": False, "span": 0.0, "iv": []})
                rec["iv"].append(iv)
                rec["span"] = G.angular_coverage(rec["iv"])
                rec["visible"] = rec["visible"] or visible
                if visible and not self.vis.get(rec["edge"], True):
                    rec["edge"] = i  # reference a visible piece when there is one
            elif curve == "Line" and len(e.Vertexes) == 2:
                p0 = (float(e.Vertexes[0].Point.x), float(e.Vertexes[0].Point.y))
                p1 = (float(e.Vertexes[1].Point.x), float(e.Vertexes[1].Point.y))
                self.lines.append((i, p0, p1, visible))
            else:
                # circles/ellipses/splines seen edge-on project to straight segments
                seg = _straight(e)
                if seg:
                    self.lines.append((i, seg[0], seg[1], visible))
        # Circles split into arcs by OCC seams are still full circles.
        self.circles = [a for a in arcs.values() if a["span"] >= 2 * math.pi - 1e-3]
        self.arcs = [a for a in arcs.values() if a["span"] < 2 * math.pi - 1e-3]
        self.bbox = G.view_local_bbox(view) or [0, 0, 0, 0]

    # model mapping -------------------------------------------------------
    def model_axis(self, local_axis):
        vec = self.xdir if local_axis == "x" else self.up
        name = G.axis_name(vec)
        if name is None:
            return None, 0
        comp = {"X": vec.x, "Y": vec.y, "Z": vec.z}[name]
        return name, (1 if comp > 0 else -1)

    def model_value(self, local_axis, local_value):
        name, sign = self.model_axis(local_axis)
        if name is None:
            return None
        c = {"X": self.center3d.x, "Y": self.center3d.y, "Z": self.center3d.z}[name]
        return c + sign * local_value

    def view_axis(self):
        return G.axis_name(self.direction)

    def find_vertex(self, pt, tol=1e-3):
        best, bd = None, tol
        for i, p in self.vertices:
            d = abs(p[0] - pt[0]) + abs(p[1] - pt[1])
            if d <= bd:
                best, bd = i, d
        return best

    def feature_points(self, local_axis):
        """Points whose ``local_axis`` coordinate is a real position on the part.

        A position along x is defined by an edge perpendicular to x (a vertical
        line), a circle centre or the corner of a slanted edge. Fillet tangent
        points are not features and are never dimensioned [04 R vs. position].
        """
        k = 0 if local_axis == "x" else 1
        o = 1 - k
        pts = []
        for i, p0, p1, visible in self.lines:
            d = (abs(p1[0] - p0[0]), abs(p1[1] - p0[1]))
            length = max(d[0], d[1], 1e-12)
            if d[k] < 1e-6 * length:  # perpendicular to the measured axis
                cands = (p0, p1)
            elif d[o] < 1e-6 * length:  # parallel: its ends are positions of other edges
                continue
            else:  # slanted (chamfer): both corners count
                cands = (p0, p1)
            for p in cands:
                idx = self.find_vertex(p)
                if idx is not None:
                    pts.append({"x": p[0], "y": p[1], "vertex": idx, "visible": visible, "kind": "corner"})
        for c in self.circles + [a for a in self.arcs if a["span"] >= math.pi - 1e-3]:
            idx = self.find_vertex(c["center"])
            if idx is not None:
                pts.append({"x": c["center"][0], "y": c["center"][1], "vertex": idx,
                            "visible": c["visible"], "kind": "center"})
        # one entry per (position, vertex); visible wins
        best = {}
        for p in pts:
            key = (round(p["x"], 3), round(p["y"], 3))
            if key not in best or (p["visible"] and not best[key]["visible"]):
                best[key] = p
        return list(best.values())


def _straight(edge, tol=1e-4):
    """Endpoints if a (non-Line) edge projects to a straight segment, else None."""
    try:
        pts = [(float(p.x), float(p.y)) for p in edge.discretize(24)]
    except Exception:
        return None
    if len(pts) < 2:
        return None
    a = min(pts)
    b = max(pts, key=lambda p: (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2)
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-6:
        return None
    for p in pts:
        if abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / L > tol * max(L, 1.0):
            return None
    return a, b


def _digits(value):
    txt = f"{value:.2f}".rstrip("0").rstrip(".")
    return len(txt)


def _side_order(occupied, axis):
    """Preferred side for a stack: horizontal dims above/below, vertical right/left."""
    prefs = ["top", "bottom"] if axis == "x" else ["right", "left"]
    free = [s for s in prefs if s not in occupied]
    return (free or prefs)[0]


def stack_depths(specs):
    """Per view and side: how many baseline lines are stacked there."""
    depth = {}
    for s in specs:
        if "side" not in s:
            continue
        d = depth.setdefault(s["view"].name, {})
        d[s["side"]] = d.get(s["side"], 0) + 1
    return depth


def side_extent(count):
    """Paper depth (mm) a stack of ``count`` lines occupies beyond the outline."""
    if not count:
        return 0.0
    return DIM_FIRST_OFFSET + (count - 1) * DIM_STEP + DIM_FONT_SIZE + 1.5


def _set_role(obj, role):
    if ROLE_PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", ROLE_PROP, "FreeDrawing", "auto-dimension role")
    setattr(obj, ROLE_PROP, role)


def make_dimension(doc, page, view, dim_type, refs, format_spec=None, x=None, y=None, role=None, name="Dimension"):
    dim = doc.addObject("TechDraw::DrawViewDimension", name)
    dim.Type = dim_type
    dim.References2D = [(view, list(refs))]
    try:
        dim.MeasureType = "Projected"
    except Exception:
        pass
    page.addView(dim)
    dim.ShowUnits = False  # units are declared once on the sheet [00][P.1]
    if format_spec:
        dim.FormatSpec = re.sub(r'%\.\d+w','%.6w',format_spec) if role else format_spec
    if x is not None:
        dim.X = x
    if y is not None:
        dim.Y = y
    if role:
        _set_role(dim, role)
    return dim


def place(specs, page, doc):
    """Create the planned dimensions at their stack / leader positions.

    Positions are offsets from the view centre in page mm (TechDraw X/Y).
    """
    created = []
    by_view_side = {}
    for s in specs:
        if "side" in s and s.get("refs"):
            by_view_side.setdefault((s["view"].name, s["side"]), []).append(s)
    for (_, side), items in by_view_side.items():
        items.sort(key=lambda s: abs(s["span"][1] - s["span"][0]))
        for level, s in enumerate(items):
            vg = s["view"]
            sc = vg.scale
            off = DIM_FIRST_OFFSET + level * DIM_STEP
            a, b = s["span"]
            mid = 0.5 * (a + b) * sc
            bb = vg.bbox
            # Text wider than the span goes outside, beyond the far extension line.
            text_w = 0.62 * DIM_FONT_SIZE * (len(s["format"].replace("%.2w", "")) + _digits(abs(b - a))) + 2.0
            span = abs(b - a) * sc
            if span < text_w + 2.0:
                direction = 1.0 if b >= a else -1.0
                mid = b * sc + direction * (text_w / 2.0 + 1.5)
            if side == "top":
                x, y = mid, bb[3] * sc + off
            elif side == "bottom":
                x, y = mid, bb[1] * sc - off
            elif side == "right":
                x, y = bb[2] * sc + off, mid
            else:
                x, y = bb[0] * sc - off, mid
            created.append(make_dimension(doc, page, vg.view, s["type"], s["refs"], s["format"], x, y, s["role"]))
    # Angles: label beside the shared vertex, away from the view centre.
    for s in specs:
        if "label_local" in s:
            vg = s["view"]
            lx, ly = s["label_local"]
            created.append(make_dimension(doc, page, vg.view, s["type"], s["refs"], s["format"],
                                          lx * vg.scale, ly * vg.scale, s["role"]))
    # Pitch circles: dash-dot cosmetic circle through the hole centres, then ⌀.
    for s in specs:
        if s.get("make_circle"):
            vg = s["view"]
            c3, r = s["make_circle"]
            try:
                vg.view.makeCosmeticCircle3d(App.Vector(*c3), r, 4, 0.25)
            except Exception:
                continue
            doc.recompute()
            lc = s["circle"]["center"]
            idx = None
            for i, e in G.iter_edges(vg.view, geom_only=False):
                if type(e.Curve).__name__ == "Circle" and abs(e.Curve.Radius - r) < 2e-3                         and abs(e.Curve.Center.x - lc[0]) < 2e-3 and abs(e.Curve.Center.y - lc[1]) < 2e-3:
                    idx = i
            if idx is not None:
                s["refs"] = [f"Edge{idx}"]
    # Radial callouts: shortest free leader. A label is accepted only when its
    # text box is clear of every view's edges, all dimension lines and
    # extension lines already placed, and other labels - in page coordinates,
    # so neighbouring views count too [E.10].
    radial = [s for s in specs if s["type"] in ("Diameter", "Radius", "Angle") and s.get("refs")]
    if not radial:
        return created
    doc.recompute()
    occ = _page_occupancy([s["view"] for s in specs], created)
    for s in radial:
        vg = s["view"]
        sc = vg.scale
        ox, oy = G.view_origin(vg.view)
        c = s["circle"]
        cx, cy = ox + c["center"][0] * sc, oy + c["center"][1] * sc
        r = c["radius"] * sc
        value = 2 * c["radius"] if s["type"] == "Diameter" else c["radius"]
        text = s.get("estimate") or s["format"].replace("%.2w", _fmt_num(value))
        tw = 0.62 * DIM_FONT_SIZE * len(text) + 1.0
        th = DIM_FONT_SIZE * 1.2
        best = None
        for k, ang in enumerate((45, 135, -45, -135, 30, 150, -30, -150, 60, 120, -60, -120, 80, 100, -80, -100)):
            dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            for step in range(22):
                t = r + 5.0 + 4.0 * step
                ex, ey = cx + dx * t, cy + dy * t  # elbow: leader meets the shoulder
                lx = ex + (tw / 2.0 if dx >= 0 else -tw / 2.0)
                ly = ey + th / 2.0
                box = [lx - tw / 2, ly - th / 2, lx + tw / 2, ly + th / 2]
                leader = (cx + dx * r, cy + dy * r, ex, ey)
                if _free(occ, box, leader):
                    cost = t + 1.5 * k
                    if best is None or cost < best[0]:
                        best = (cost, (lx, ly), box, leader)
                    break
        if best is None:
            pos = (cx + r + 12.0 + tw / 2, cy + r + 12.0)
            box = [pos[0] - tw / 2, pos[1] - th / 2, pos[0] + tw / 2, pos[1] + th / 2]
            leader = None
        else:
            _, pos, box, leader = best
        occ["boxes"].append(G.expand(box, 1.0))
        if leader:
            occ["leaders"].append(leader)
        created.append(make_dimension(doc, page, vg.view, s["type"], s["refs"], s["format"],
                                      pos[0] - ox, pos[1] - oy, s["role"]))
    return created


def _fmt_num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _page_occupancy(view_geoms, dims):
    """Obstacles on the sheet in page mm: view edges, placed dimension lines and texts."""
    from .snapshot import _dimension_record

    segs, boxes = [], []
    seen = set()
    for vg in view_geoms:
        if vg.name in seen:
            continue
        seen.add(vg.name)
        ox, oy = G.view_origin(vg.view)
        sc = vg.scale
        for _, e in G.iter_edges(vg.view, geom_only=False):  # includes PCD / cosmetic circles
            pts = G.discretize(e, 1.0)
            for p, q in zip(pts, pts[1:]):
                segs.append((ox + p[0] * sc, oy + p[1] * sc, ox + q[0] * sc, oy + q[1] * sc))
    for d in dims:
        try:
            rec = _dimension_record(d)
        except Exception:
            continue
        segs.extend(tuple(l) for l in rec.get("lines", []))
        if rec.get("text_box"):
            boxes.append(G.expand(rec["text_box"], 0.8))
    return {"segs": segs, "boxes": boxes, "leaders": []}


def _free(occ, box, leader):
    pad = G.expand(box, 0.8)
    if any(G.seg_hits_box(sg, pad) for sg in occ["segs"]):
        return False
    if any(G.boxes_overlap(pad, b) for b in occ["boxes"]):
        return False
    if any(G.seg_hits_box(ld, pad) for ld in occ["leaders"]):
        return False
    if any(G.seg_hits_box(leader, b) for b in occ["boxes"]):
        return False
    return True


def dedupe(page, doc):
    """Remove dimensions that control the same thing twice [E.11][P.5].

    Duplicate = same measured value and same references resolved to the same
    model-space feature (same type, same view geometry), or identical value
    along the same model axis between the same model coordinates.
    """
    from .snapshot import dimension_signature

    seen = {}
    removed = []
    for obj in list(page.Views):
        if obj.TypeId != "TechDraw::DrawViewDimension":
            continue
        try:
            sig = dimension_signature(obj)
        except Exception:
            sig = None
        if sig is None:
            continue
        if sig in seen:
            removed.append(obj.Name)
            doc.removeObject(obj.Name)
        else:
            seen[sig] = obj.Name
    return removed
