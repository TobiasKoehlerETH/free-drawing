"""Extract a JSON snapshot of a TechDraw page for rule checking and rendering.

The snapshot is the single contract between FreeCAD and the rules engine
(``free_drawing_mcp.rules``): everything an agent needs to judge the sheet
against ``guidelines/CHECKLIST.md``, in page coordinates (mm, origin
bottom-left, Y up), without any FreeCAD types.
"""

import json
import math
import re

import FreeCAD as App

from . import geometry as G
from . import templates
from .standards import DIM_FONT_SIZE, LINE_THICK, LINE_THIN
from .textfmt import display_text

SCHEMA_VERSION = 1
META_PROP = "FDMeta"

_VIEW_PART = "TechDraw::DrawViewPart"
_KIND_BY_TYPE = {
    "TechDraw::DrawViewSection": "section",
    "TechDraw::DrawComplexSection": "section",
    "TechDraw::DrawViewDetail": "detail",
    "TechDraw::DrawViewImage": "image",
    "TechDraw::DrawViewSpreadsheet": "table",
    "TechDraw::DrawViewSymbol": "symbol",
    "TechDraw::DrawViewDraft": "draft",
    "TechDraw::DrawViewArch": "arch",
}


def r2(v):
    return round(float(v), 2)


def rbox(b):
    return [r2(v) for v in b] if b else None


def get_meta(page):
    if META_PROP not in page.PropertiesList:
        return {}
    try:
        return json.loads(getattr(page, META_PROP) or "{}")
    except ValueError:
        return {}


def set_meta(page, **updates):
    meta = get_meta(page)
    meta.update(updates)
    if META_PROP not in page.PropertiesList:
        page.addProperty("App::PropertyString", META_PROP, "FreeDrawing", "free-drawing metadata (JSON)")
    setattr(page, META_PROP, json.dumps(meta, ensure_ascii=False))
    return meta


def page_views(page):
    """All drawable children, projection-group items flattened."""
    out = []
    for obj in page.Views:
        out.append(obj)
        if obj.TypeId == "TechDraw::DrawProjGroup":
            out.extend(obj.Views)
    return out


def _projection(page):
    for obj in page.Views:
        if obj.TypeId == "TechDraw::DrawProjGroup":
            pt = str(obj.ProjectionType).lower()
            if pt.startswith("first"):
                return "first"
            if pt.startswith("third"):
                return "third"
            try:
                pref = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General").GetInt("ProjectionAngle", 0)
                return "third" if pref == 1 else "first"
            except Exception:
                return "first"
    return None


def _source_center(view):
    shapes = []
    for src in list(getattr(view, "Source", []) or []) + list(getattr(view, "XSource", []) or []):
        try:
            shapes.append(src.Shape)
        except Exception:
            pass
    if not shapes:
        return App.Vector(0, 0, 0)
    bb = G.exact_bbox(shapes[0])
    for s in shapes[1:]:
        bb.add(G.exact_bbox(s))
    return bb.Center


def _view_kind(view):
    for t, k in _KIND_BY_TYPE.items():
        if view.TypeId == t:
            return k
    if view.TypeId == "TechDraw::DrawProjGroup":
        return "group"
    if G.is_type(view, _VIEW_PART):
        if G.proj_group_of(view) is not None:
            return "ortho"
        return "ortho" if G.is_principal(view.Direction) else "pictorial"
    return "other"


def _line_widths(view):
    vo = getattr(view, "ViewObject", None)
    if vo is not None:
        try:
            return {"visible": float(vo.LineWidth), "hidden": float(vo.HiddenWidth),
                    "center": float(getattr(vo, "ExtraWidth", vo.HiddenWidth)), "source": "view"}
        except Exception:
            pass
    return {"visible": LINE_THICK, "hidden": LINE_THIN, "center": LINE_THIN, "source": "line_group_default"}


def _segments(view, edges, vis, origin, scale, max_seg=1.5):
    out_v, out_h = [], []
    for i, e in edges:
        pts = G.discretize(e, max_seg=max_seg / max(scale, 1e-6))
        page_pts = [G.local_to_page(view, p, origin, scale) for p in pts]
        flat = [r2(c) for p in page_pts for c in p]
        (out_v if vis.get(i, True) else out_h).append(flat)
    return out_v, out_h


def _cosmetic(view, origin, scale):
    out = []
    for index, edge in G.iter_edges(view, geom_only=False):
        auxiliary = G.auxiliary_edge(view, index)
        if auxiliary is None:
            continue
        obj, kind = auxiliary
        fmt = dict(getattr(obj, "Format", {}) or {})
        pts = [G.local_to_page(view, p, origin, scale) for p in G.discretize(edge)]
        rec = {"tag": str(obj.Tag), "kind": kind, "edge": f"Edge{index}",
               "geometry": "line" if type(edge.Curve).__name__ == "Line" else "polyline",
               "points": [r2(c) for p in pts for c in p], "format": fmt}
        if type(edge.Curve).__name__ == "Circle" and edge.isClosed():
            center = edge.Curve.Center
            rec.update(geometry="circle", center=[r2(c) for c in G.local_to_page(
                view, (center.x, center.y), origin, scale)], radius=r2(edge.Curve.Radius * scale))
        out.append(rec)
    return out


def _cosmetic_tags(view):
    tags = []
    try:
        for ce in view.CosmeticEdges:
            tag = getattr(ce, "Tag", None)
            if tag:
                tags.append(tag)
    except Exception:
        pass
    return tags


def _center_line_tags(view):
    tags = []
    try:
        for cl in view.CenterLines:
            tag = getattr(cl, "Tag", None)
            if tag:
                tags.append(tag)
    except Exception:
        pass
    return tags


def _seg_point_dist(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _image_path(view):
    import os

    for prop in ("ImageIncluded", "ImageFile"):
        path = str(getattr(view, prop, "") or "")
        if path and os.path.isfile(path):
            return path
    return ""


def _view_record(view, include_geometry):
    kind = _view_kind(view)
    rec = {
        "name": view.Name,
        "label": view.Label,
        "type_id": view.TypeId,
        "kind": kind,
    }
    if kind == "group":
        rec.update(projection=str(view.ProjectionType), x=r2(view.X), y=r2(view.Y), scale=float(view.Scale))
        return rec
    origin = G.view_origin(view)
    rec["center"] = [r2(origin[0]), r2(origin[1])]
    rec["caption"] = str(getattr(view, "Caption", "") or "")
    if view.TypeId == "TechDraw::DrawViewImage":
        w, h = float(view.Width), float(view.Height)
        rec.update(bbox=rbox([origin[0] - w / 2, origin[1] - h / 2, origin[0] + w / 2, origin[1] + h / 2]),
                   scale=float(getattr(view, "FDScale", view.Scale)), shaded=True, hard_hidden=False,
                   image_file=_image_path(view))
        if "FDDirection" in view.PropertiesList:
            rec["kind"] = "pictorial"
            rec["direction"] = [r2(c) for c in view.FDDirection]
        return rec
    if not G.is_type(view, _VIEW_PART):
        rec["bbox"] = None
        return rec
    scale = G.view_scale(view)
    edges = list(G.iter_edges(view))
    vis = G.visibility_map(view)
    grp = G.proj_group_of(view)
    rec.update(
        scale=scale,
        projection_type=str(getattr(view, "Type", "")) if grp is not None else None,
        group=grp.Name if grp is not None else None,
        direction=[r2(c) for c in view.Direction],
        x_direction=[r2(c) for c in view.XDirection],
        axis=G.axis_name(App.Vector(view.Direction)),
        hard_hidden=bool(getattr(view, "HardHidden", False)),
        smooth_visible=bool(getattr(view, "SmoothVisible", False)),
        edges_visible=sum(1 for v in vis.values() if v),
        edges_hidden=sum(1 for v in vis.values() if not v),
        bbox=rbox(G.view_page_bbox(view)),
        line_widths=_line_widths(view),
        shaded=kind == "image",
    )
    if kind == "section":
        # cutting plane on the base view, hatch loops, half-section axis [03]
        rec["section_symbol"] = str(getattr(view, "SectionSymbol", "") or "")
        base = getattr(view, "BaseView", None)
        rec["base_view"] = base.Name if base is not None else None
        try:
            from . import sections as _sections

            rec.update(_sections.section_record(view))
        except Exception as e:
            rec["section_error"] = f"{type(e).__name__}: {e}"
    if kind == "detail":
        # detail circle + letter on the base view, detail scale [03 rule 5]
        rec["reference"] = str(getattr(view, "Reference", "") or "")
        base = getattr(view, "BaseView", None)
        rec["base_view"] = base.Name if base is not None else None
        try:
            from . import sections as _sections

            rec.update(_sections.detail_record(view))
        except Exception as e:
            rec["detail_error"] = f"{type(e).__name__}: {e}"
    cosmetic = _cosmetic(view, origin, scale)
    if rec.get("axis_line"):
        cosmetic.append({"tag": "half-section-axis", "kind": "centerline", "points": rec["axis_line"]})
    circles = []
    for i, e in edges:
        if type(e.Curve).__name__ != "Circle":
            continue
        c = e.Curve.Center
        full = len(e.Vertexes) == 1 or e.isClosed()
        cp = G.local_to_page(view, (c.x, c.y), origin, scale)
        r = float(e.Curve.Radius) * scale
        marked = False
        for cm in cosmetic:
            if cm.get("geometry", "line") != "line" or not cm.get("format", {}).get("visible", True):
                continue
            p = cm["points"]
            if len(p) < 4:
                continue
            if _seg_point_dist(cp, (p[0], p[1]), (p[2], p[3])) < 0.3 and math.hypot(p[2] - p[0], p[3] - p[1]) > r:
                marked = True
                break
        circles.append({"edge": f"Edge{i}", "center": [r2(cp[0]), r2(cp[1])], "radius": r2(r),
                        "model_radius": r2(e.Curve.Radius), "full": bool(full),
                        "visible": bool(vis.get(i, True)), "center_mark": marked})
    # OCC splits circles at seams: equal centre + radius covering 360 deg is full.
    groups = {}
    for (i, e), c in zip([ie for ie in edges if type(ie[1].Curve).__name__ == "Circle"], circles):
        iv = (0.0, 2 * math.pi) if c["full"] else G.arc_interval(e)
        key = (c["center"][0], c["center"][1], c["radius"])
        groups.setdefault(key, []).append((iv, c))
    merged = []
    for key, items in groups.items():
        first = dict(items[0][1])
        first["full"] = G.angular_coverage([iv for iv, _ in items]) >= 2 * math.pi - 1e-3
        first["visible"] = any(c["visible"] for _, c in items)
        first["center_mark"] = any(c["center_mark"] for _, c in items)
        first["edges"] = [c["edge"] for _, c in items]
        merged.append(first)
    circles = merged
    vo = getattr(view, "ViewObject", None)
    arc_marks = bool(getattr(vo, "ArcCenterMarks", False)) if vo is not None else False
    if arc_marks:
        for c in circles:
            c["center_mark"] = True
    rec["circles"] = circles
    rec["cosmetic"] = cosmetic
    if include_geometry:
        segv, segh = _segments(view, edges, vis, origin, scale)
        if rec.get("axis_line"):
            # half section: the cut boundary on the axis is drawn as the centre line [03 rule 4]
            al = rec["axis_line"]
            segv = [pl for pl in segv if not all(
                _seg_point_dist((pl[i], pl[i + 1]), (al[0], al[1]), (al[2], al[3])) < 0.05 for i in range(0, len(pl) - 1, 2))]
        rec["polylines_visible"] = segv
        rec["polylines_hidden"] = segh
    return rec


def _text_box(cx, cy, text, size=DIM_FONT_SIZE, vertical=False):
    w = max(1.0, 0.62 * size * len(text))
    h = size * 1.15
    if vertical:
        w, h = h, w
    return [r2(cx - w / 2), r2(cy - h / 2), r2(cx + w / 2), r2(cy + h / 2)]


def _model_axis_of(view, local_axis):
    xdir = App.Vector(view.XDirection)
    vec = xdir if local_axis == "x" else G.up_vector(App.Vector(view.Direction), xdir)
    name = G.axis_name(vec)
    if name is None:
        return None, 0
    comp = {"X": vec.x, "Y": vec.y, "Z": vec.z}[name]
    return name, (1 if comp > 0 else -1)


def _ref_points_local(view, subs):
    pts = []
    for s in subs:
        m = re.match(r"(Vertex|Edge)(\d+)$", s)
        if not m:
            continue
        try:
            if m.group(1) == "Vertex":
                p = view.getVertexByIndex(int(m.group(2))).Point
                pts.append((float(p.x), float(p.y)))
            else:
                e = view.getEdgeByIndex(int(m.group(2)))
                if type(e.Curve).__name__ == "Circle":
                    c = e.Curve.Center
                    pts.append((float(c.x), float(c.y)))
                else:
                    for v in e.Vertexes:
                        pts.append((float(v.Point.x), float(v.Point.y)))
        except Exception:
            continue
    return pts


def _refs_hidden(view, subs):
    vis = G.visibility_map(view)
    hidden = False
    vis_vertices = None
    for s in subs:
        m = re.match(r"(Vertex|Edge)(\d+)$", s)
        if not m:
            continue
        idx = int(m.group(2))
        if m.group(1) == "Edge":
            if not vis.get(idx, True):
                hidden = True
        else:
            if vis_vertices is None:
                vis_vertices = set()
                for i, e in G.iter_edges(view):
                    if not vis.get(i, True):
                        continue
                    for v in e.Vertexes:
                        vis_vertices.add((round(v.Point.x, 2), round(v.Point.y, 2)))
                    if type(e.Curve).__name__ == "Circle":
                        c = e.Curve.Center
                        vis_vertices.add((round(c.x, 2), round(c.y, 2)))
            try:
                p = view.getVertexByIndex(idx).Point
                if (round(p.x, 2), round(p.y, 2)) not in vis_vertices:
                    hidden = True
            except Exception:
                pass
    return hidden


def dimension_signature(dim):
    """Hashable identity of what a dimension controls (for dedupe / E.11)."""
    refs = list(dim.References2D)
    if not refs:
        return None
    view, subs = refs[0][0], list(refs[0][1])
    if not G.is_type(view, _VIEW_PART):
        return None
    value = round(float(dim.getRawValue()), 3)
    t = str(dim.Type)
    center = _source_center(view)
    pts = _ref_points_local(view, subs)
    if t in ("Diameter", "Radius"):
        # Same size on the same model cylinder = same requirement.
        axis = G.axis_name(App.Vector(view.Direction))
        if pts:
            c = pts[0]
            mx = _model_point(view, c, center)
            key = tuple(round(v, 2) for v in mx)
            if axis:
                key = tuple(v for i, v in enumerate(key) if "XYZ"[i] != axis)
            return ("radial", t, value, axis, key)
        return ("radial", t, value, axis)
    if t in ("DistanceX", "DistanceY"):
        local_axis = "x" if t == "DistanceX" else "y"
        axis, sign = _model_axis_of(view, local_axis)
        if axis is None or len(pts) < 2:
            return ("linear", t, value, view.Name, tuple(subs))
        vals = sorted(round(_model_coord(view, axis, sign, p, local_axis, center), 2) for p in pts[:2])
        return ("linear", axis, value, tuple(vals))
    return (t, value, view.Name, tuple(sorted(subs)))


def _model_point(view, p, center):
    xdir = App.Vector(view.XDirection)
    up = G.up_vector(App.Vector(view.Direction), xdir)
    m = center + xdir * p[0] + up * p[1]
    return (m.x, m.y, m.z)


def _model_coord(view, axis, sign, p, local_axis, center):
    c = {"X": center.x, "Y": center.y, "Z": center.z}[axis]
    return c + sign * (p[0] if local_axis == "x" else p[1])


def _dimension_record(dim):
    refs = list(dim.References2D)
    view = refs[0][0] if refs else None
    subs = list(refs[0][1]) if refs else []
    t = str(dim.Type)
    text = display_text(dim)
    rec = {
        "name": dim.Name,
        "label": dim.Label,
        "type": t,
        "view": view.Name if view is not None else None,
        "refs": subs,
        "value": r2(dim.getRawValue()) if refs else None,
        "text": text,
        "format_spec": str(dim.FormatSpec),
        "arbitrary": bool(dim.Arbitrary),
        "show_units": bool(dim.ShowUnits),
        "theoretical_exact": bool(getattr(dim, "TheoreticalExact", False)),
        "tolerance": {
            "over": r2(getattr(dim.OverTolerance, "Value", dim.OverTolerance)),
            "under": r2(getattr(dim.UnderTolerance, "Value", dim.UnderTolerance)),
            "equal": bool(dim.EqualTolerance),
        },
        "role": str(getattr(dim, "FDRole", "") or ""),
        "label_xy": [r2(dim.X), r2(dim.Y)],
    }
    if view is None or not G.is_type(view, _VIEW_PART):
        rec.update(text_box=None, lines=[], ref_hidden=False, model_axis=None, signature=None)
        return rec
    origin = G.view_origin(view)
    cx, cy = origin[0] + float(dim.X), origin[1] + float(dim.Y)
    lines = []
    vertical = False
    try:
        if t in ("DistanceX", "DistanceY", "Distance"):
            p1, p2 = [G.dim_internal_to_page(view, (p.x, p.y), origin) for p in dim.getLinearPoints()]
            if t == "DistanceX":
                yl = cy - 0.5 * DIM_FONT_SIZE - 0.6
                lines.append([p1[0], yl, p2[0], yl])  # dimension line
                lines.append([p1[0], p1[1], p1[0], yl])  # extension lines
                lines.append([p2[0], p2[1], p2[0], yl])
            elif t == "DistanceY":
                vertical = True
                xl = cx + 0.5 * DIM_FONT_SIZE + 0.6
                lines.append([xl, p1[1], xl, p2[1]])
                lines.append([p1[0], p1[1], xl, p1[1]])
                lines.append([p2[0], p2[1], xl, p2[1]])
            else:
                lines.append([p1[0], p1[1], p2[0], p2[1]])
        elif t in ("Diameter", "Radius"):
            ap = dim.getArcPoints()
            center = G.dim_internal_to_page(view, (ap[0].x, ap[0].y), origin)
            on = [G.dim_internal_to_page(view, (p.x, p.y), origin) for p in ap[1:3]]
            r = G.dist(center, on[0]) if on else 0.0
            ang = math.atan2(cy - center[1], cx - center[0])
            near = (center[0] + r * math.cos(ang), center[1] + r * math.sin(ang))
            start = near  # one-sided leader from the circle to the label (ISO / SolidWorks style)
            # leader ends at the text edge, not its centre
            back = 0.5 * 0.62 * DIM_FONT_SIZE * len(text)
            end = (cx - back * math.cos(ang), cy - back * math.sin(ang))
            lines.append([start[0], start[1], end[0], end[1]])
            rec["arc"] = {"center": [r2(center[0]), r2(center[1])], "radius": r2(r)}
    except Exception:
        pass
    rec["lines"] = [[r2(v) for v in seg] for seg in lines]
    rec["text_box"] = _text_box(cx, cy, text, vertical=vertical)
    rec["text_center"] = [r2(cx), r2(cy)]
    rec["ref_hidden"] = _refs_hidden(view, subs)
    if t in ("DistanceX", "DistanceY"):
        axis, _ = _model_axis_of(view, "x" if t == "DistanceX" else "y")
        rec["model_axis"] = axis
    else:
        rec["model_axis"] = None
    try:
        sig = dimension_signature(dim)
        rec["signature"] = json.dumps(sig) if sig is not None else None
    except Exception:
        rec["signature"] = None
    return rec


def _anno_text_size(obj):
    try:
        return float(getattr(obj.TextSize, "Value", obj.TextSize))
    except Exception:
        return 5.0


def _annotation_record(obj):
    t = obj.TypeId
    rec = {"name": obj.Name, "label": obj.Label, "type_id": t}
    try:
        origin = G.view_origin(obj)
    except Exception:
        origin = (0.0, 0.0)
    if t == "TechDraw::DrawViewAnnotation":
        lines = [str(s) for s in obj.Text]
        size = _anno_text_size(obj)
        w = max([0.6 * size * len(s) for s in lines] + [1.0])
        h = len(lines) * size * 1.5
        rec.update(kind="note", text=lines, size=r2(size),
                   bbox=[r2(origin[0] - w / 2), r2(origin[1] - h / 2), r2(origin[0] + w / 2), r2(origin[1] + h / 2)])
    elif t == "TechDraw::DrawRichAnno":
        raw = str(getattr(obj, "AnnoText", ""))
        text = re.sub(r"<[^>]+>", " ", raw)
        text = [s.strip() for s in re.split(r"\s{2,}|\n", text) if s.strip()]
        parent = getattr(obj, "AnnoParent", None)
        if parent is not None:
            po = G.view_origin(parent)
            origin = (po[0] + float(obj.X), po[1] + float(obj.Y))
        rec.update(kind="rich", text=text, bbox=[r2(origin[0] - 15), r2(origin[1] - 5), r2(origin[0] + 15), r2(origin[1] + 5)])
    elif t == "TechDraw::DrawViewBalloon":
        rec.update(kind="balloon", text=[str(obj.Text)], bbox=[r2(origin[0] - 5), r2(origin[1] - 5), r2(origin[0] + 5), r2(origin[1] + 5)])
    elif t == "TechDraw::DrawWeldSymbol" or t == "TechDraw::DrawTileWeld":
        rec.update(kind="weld", text=[], bbox=None)
    elif t == "TechDraw::DrawLeaderLine":
        rec.update(kind="leader", text=[], bbox=None)
    elif t == "TechDraw::DrawViewSymbol":
        svg = str(getattr(obj, "Symbol", ""))
        texts = [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)]
        rec.update(kind="symbol", text=[s for s in texts if s], bbox=[r2(origin[0] - 6), r2(origin[1] - 6), r2(origin[0] + 6), r2(origin[1] + 6)])
    else:
        rec.update(kind="other", text=[], bbox=None)
    return rec


def snapshot(doc, page, include_geometry=True):
    tpl = page.Template
    sheet = {"width": float(page.PageWidth), "height": float(page.PageHeight)}
    fields = {}
    if tpl is not None:
        try:
            sheet.update(templates.parse_template(str(tpl.Template), sheet["width"], sheet["height"]))
        except Exception as e:
            sheet["template_error"] = str(e)
        try:
            fields = {str(k): str(v) for k, v in dict(tpl.EditableTexts).items()}
        except Exception:
            fields = {}
    sheet["size_name"] = templates.sheet_name(sheet["width"], sheet["height"])
    sheet["orientation"] = "landscape" if sheet["width"] >= sheet["height"] else "portrait"

    views, dims, annos = [], [], []
    for obj in page_views(page):
        t = obj.TypeId
        if t == "TechDraw::DrawViewDimension":
            dims.append(_dimension_record(obj))
        elif t in ("TechDraw::DrawViewAnnotation", "TechDraw::DrawRichAnno", "TechDraw::DrawViewBalloon",
                   "TechDraw::DrawWeldSymbol", "TechDraw::DrawLeaderLine", "TechDraw::DrawTileWeld"):
            annos.append(_annotation_record(obj))
        elif t == "TechDraw::DrawViewSymbol":
            annos.append(_annotation_record(obj))
        elif t == "TechDraw::DrawHatch" or t == "TechDraw::DrawGeomHatch":
            continue
        else:
            views.append(_view_record(obj, include_geometry))
    meta = get_meta(page)
    solids = 0
    src_names = set()
    bb = None
    for obj in page_views(page):
        for src in list(getattr(obj, "Source", []) or []):
            src_names.add(src.Name)
            try:
                solids = max(solids, len(src.Shape.Solids))
                if bb is None:
                    bb = App.BoundBox(G.exact_bbox(src.Shape))
                else:
                    bb.add(G.exact_bbox(src.Shape))
            except Exception:
                pass
    extents = {"X": r2(bb.XLength), "Y": r2(bb.YLength), "Z": r2(bb.ZLength)} if bb else {}
    return {
        "schema": SCHEMA_VERSION,
        "document": doc.Name,
        "file": doc.FileName or None,
        "page": page.Name,
        "page_label": page.Label,
        "units": "mm",
        "sheet": sheet,
        "title_block": fields,
        "projection": _projection(page),
        "views": views,
        "dimensions": dims,
        "annotations": annos,
        "meta": meta,
        "model": {"sources": sorted(src_names), "solids": solids, "extents": extents},
        "gui": bool(App.GuiUp),
    }
