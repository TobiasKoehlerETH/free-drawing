"""Coordinate frames used by TechDraw, made explicit.

TechDraw mixes three frames; mixing them up is the classic source of
misplaced dimensions:

* page frame   - mm, origin bottom-left of the sheet, Y up. Used by every
                 public function in this package.
* view-local   - unscaled model mm centred on the view, Y up. Returned by
                 ``getEdgeByIndex`` / ``getVertexByIndex`` and cosmetic edges.
* dim-internal - scaled mm centred on the view, Y down. Returned by
                 ``DrawViewDimension.getLinearPoints`` / ``getArcPoints``.

Dimension ``X``/``Y`` (the label centre) and projection-group item ``X``/``Y``
are page-mm offsets from their parent, Y up.
"""

import math

import FreeCAD as App


def is_type(obj, *type_ids):
    try:
        return any(obj.isDerivedFrom(t) for t in type_ids)
    except Exception:
        return False


def proj_group_of(view):
    for parent in getattr(view, "InList", []):
        if parent.TypeId == "TechDraw::DrawProjGroup":
            return parent
    return None


def view_origin(view):
    """Page position of a view's centre (adds the projection group offset)."""
    x, y = float(view.X), float(view.Y)
    grp = proj_group_of(view)
    if grp is not None:
        x += float(grp.X)
        y += float(grp.Y)
    return x, y


def set_view_origin(view, x, y):
    grp = proj_group_of(view)
    if grp is not None:
        view.X = x - float(grp.X)
        view.Y = y - float(grp.Y)
    else:
        view.X = x
        view.Y = y


def view_scale(view):
    try:
        return float(view.getScale())
    except Exception:
        return float(getattr(view, "Scale", 1.0))


def local_to_page(view, pt, origin=None, scale=None):
    ox, oy = origin or view_origin(view)
    s = view_scale(view) if scale is None else scale
    return (ox + s * pt[0], oy + s * pt[1])


def dim_internal_to_page(view, pt, origin=None):
    ox, oy = origin or view_origin(view)
    return (ox + pt[0], oy - pt[1])


def _has_source_geometry(view):
    for src in list(getattr(view, "Source", []) or []) + list(getattr(view, "XSource", []) or []):
        try:
            if not src.Shape.isNull() and src.Shape.Faces:
                return True
        except Exception:
            pass
    return False


def wait_for_geometry(view, timeout=120.0):
    """In the GUI, TechDraw runs hidden-line removal in a worker thread after a
    recompute; block (while pumping GUI events) until the projection exists.
    Console mode is synchronous, so this returns immediately there."""
    if not App.GuiUp or not is_type(view, "TechDraw::DrawViewPart"):
        return True
    import time

    deadline = time.monotonic() + timeout
    while True:
        try:
            if view.getVisibleEdges() or view.getHiddenEdges():
                return True
        except Exception:
            pass
        if not _has_source_geometry(view) or time.monotonic() > deadline:
            return False
        try:
            import FreeCADGui
            from PySide import QtWidgets

            FreeCADGui.updateGui()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        time.sleep(0.05)


def geometry_edge_count(view):
    """Number of projected (non-cosmetic) edges; cosmetic/centre lines follow them."""
    wait_for_geometry(view)
    try:
        return len(view.getVisibleEdges()) + len(view.getHiddenEdges())
    except Exception:
        return None


def iter_edges(view, limit=5000, geom_only=True):
    """Yield ``(index, edge)`` for a view's geometry in view-local frame.

    ``geom_only`` skips cosmetic edges and centre lines TechDraw appends.
    """
    if geom_only:
        n = geometry_edge_count(view)
        if n is not None:
            limit = min(limit, n)
    for i in range(limit):
        try:
            e = view.getEdgeByIndex(i)
        except Exception:
            return
        yield i, e


def iter_vertices(view, limit=5000):
    wait_for_geometry(view)
    for i in range(limit):
        try:
            v = view.getVertexByIndex(i)
        except Exception:
            return
        yield i, (float(v.Point.x), float(v.Point.y))


def _flip_scaled(points, scale):
    s = scale or 1.0
    return [(p[0] / s, -p[1] / s) for p in points]


def _edge_key(edge, scale=None, flip=False):
    pts = [(float(v.Point.x), float(v.Point.y)) for v in edge.Vertexes]
    if flip:
        pts = _flip_scaled(pts, scale)
    try:
        mid = edge.valueAt(edge.FirstParameter + 0.5 * (edge.LastParameter - edge.FirstParameter))
        m = (float(mid.x), float(mid.y))
        if flip:
            m = _flip_scaled([m], scale)[0]
    except Exception:
        m = (0.0, 0.0)
    pts = sorted((round(p[0], 2), round(p[1], 2)) for p in pts)
    return (tuple(pts), (round(m[0], 2), round(m[1], 2)))


def visibility_map(view):
    """Map edge index -> True (visible) / False (hidden).

    ``getVisibleEdges`` returns scaled, Y-down copies; match them back to the
    indexed (unscaled, Y-up) edges by endpoints and midpoint.
    """
    s = view_scale(view)
    wait_for_geometry(view)
    try:
        visible = {_edge_key(e, s, True) for e in view.getVisibleEdges()}
    except Exception:
        visible = set()
    out = {}
    for i, e in iter_edges(view):
        out[i] = _edge_key(e) in visible
    return out


def discretize(edge, max_seg=1.0, max_pts=96):
    """Polyline points (view-local) approximating an edge."""
    try:
        length = edge.Length
    except Exception:
        length = 0.0
    curve = type(edge.Curve).__name__
    if curve == "Line" or length <= 1e-9:
        return [(float(v.Point.x), float(v.Point.y)) for v in edge.Vertexes] or []
    n = int(min(max_pts, max(8, length / max_seg)))
    try:
        return [(float(p.x), float(p.y)) for p in edge.discretize(n)]
    except Exception:
        return [(float(v.Point.x), float(v.Point.y)) for v in edge.Vertexes]


def bbox_of(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def view_local_bbox(view):
    pts = []
    for _, e in iter_edges(view):
        bb = e.BoundBox
        pts.append((bb.XMin, bb.YMin))
        pts.append((bb.XMax, bb.YMax))
    return bbox_of(pts)


def view_page_bbox(view):
    lb = view_local_bbox(view)
    if lb is None:
        ox, oy = view_origin(view)
        return [ox, oy, ox, oy]
    a = local_to_page(view, (lb[0], lb[1]))
    b = local_to_page(view, (lb[2], lb[3]))
    return [round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3)]


def up_vector(direction, xdir):
    """View-local +Y in model space (TechDraw: Direction x XDirection)."""
    d = App.Vector(direction)
    x = App.Vector(xdir)
    up = d.cross(x)
    if up.Length < 1e-9:
        return App.Vector(0, 0, 1)
    up.normalize()
    return up


def axis_name(vec, tol=1e-6):
    """'X', 'Y' or 'Z' (sign dropped) for a principal direction, else None."""
    comps = [abs(vec.x), abs(vec.y), abs(vec.z)]
    m = max(comps)
    if m < tol:
        return None
    if sum(1 for c in comps if c > 1e-3 * m) > 1:
        return None
    return "XYZ"[comps.index(m)]


def is_principal(vec):
    return axis_name(App.Vector(vec)) is not None


def boxes_overlap(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def expand(b, d):
    return [b[0] - d, b[1] - d, b[2] + d, b[3] + d]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def seg_hits_box(seg, box):
    """True if segment ``(x1, y1, x2, y2)`` touches box ``[x0, y0, x1, y1]``."""
    x1, y1, x2, y2 = seg
    bx0, by0, bx1, by1 = box
    if max(x1, x2) < bx0 or min(x1, x2) > bx1 or max(y1, y2) < by0 or min(y1, y2) > by1:
        return False
    if bx0 <= x1 <= bx1 and by0 <= y1 <= by1:
        return True
    if bx0 <= x2 <= bx1 and by0 <= y2 <= by1:
        return True
    # Liang-Barsky clip
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx0), (dx, bx1 - x1), (-dy, y1 - by0), (dy, by1 - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def exact_bbox(shape):
    """Tight bounding box (OCC's default box is padded by tolerances)."""
    try:
        return shape.optimalBoundingBox(False, False)
    except Exception:
        return shape.BoundBox


def arc_interval(edge):
    """(start, end) angle of a circular edge in its own plane, radians, start < end."""
    try:
        c = edge.Curve.Center
        a0 = edge.Vertexes[0].Point - c
        mid = edge.valueAt(0.5 * (edge.FirstParameter + edge.LastParameter)) - c
        a1 = edge.Vertexes[-1].Point - c
        t0 = math.atan2(a0.y, a0.x)
        tm = math.atan2(mid.y, mid.x)
        t1 = math.atan2(a1.y, a1.x)
        span = abs(edge.LastParameter - edge.FirstParameter)
        # orient so the midpoint lies inside [t0, t0 + span]
        if ((tm - t0) % (2 * math.pi)) <= span + 1e-9:
            return t0, t0 + span
        return t1, t1 + span
    except Exception:
        return 0.0, abs(edge.LastParameter - edge.FirstParameter)


def angular_coverage(intervals):
    """Total covered angle of a set of (start, end) intervals on the circle."""
    two_pi = 2 * math.pi
    segs = []
    for a, b in intervals:
        if b - a >= two_pi - 1e-6:
            return two_pi
        a %= two_pi
        b = a + (b - a)
        if b > two_pi:
            segs.extend([(a, two_pi), (0.0, b - two_pi)])
        else:
            segs.append((a, b))
    segs.sort()
    total, cur_a, cur_b = 0.0, None, None
    for a, b in segs:
        if cur_b is None or a > cur_b + 1e-6:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total
