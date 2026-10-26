"""Repack complete view footprints after dimensions move into sections/details."""
from . import dimensions, geometry as G, sections
from .annotation_layout import clear_collisions
from .layout_policy import pack_blocks
from .snapshot import _annotation_record, _dimension_record, dimension_signature
from .standards import SHEET_MARGIN


def maximize_scale(page, group, names, views, specs, created, scale, scales, arrange, projection, keepouts):
    """Try larger shared scales using actual dimension lines and text footprints.

    Extra views keep their geometry references; standard views and sections
    share a scale. Details remain enlarged. On failure restore the prior layout.
    """
    doc = page.Document
    active = {s["view"].view.Name for s in specs}
    extra = list({s["view"].view.Name: s["view"].view for s in specs
                  if s["view"].view.Name not in {v.Name for v in views.values()}}.values())
    detail_scales = {v.Name: float(v.Scale) for v in extra if v.TypeId == "TechDraw::DrawViewDetail"}
    # FreeCAD can retain stale projected geometry after changing Detail.Scale.
    # Keep detail scales fixed; resizing them requires rebuilding references.
    candidates = [s for s in scales if not detail_scales or s < min(detail_scales.values())]
    original = [(v, float(v.X), float(v.Y), float(v.Scale), str(v.Caption)) for v in [group] + extra]
    original_offsets = [(v, float(v.X), float(v.Y)) for v in views.values()]
    notes = [v for v in page.Views if v.TypeId == "TechDraw::DrawViewAnnotation" and v.Name.startswith("Notes")]
    thickness_notes = [v for v in page.Views if v.TypeId == "TechDraw::DrawViewAnnotation" and v.Name.startswith("Thickness")]
    original_notes = [(v, float(v.X), float(v.Y)) for v in notes + thickness_notes]
    expected = measurements(created)
    geoms = {s["view"].view.Name: s["view"] for s in specs}
    boxes = {n: G.view_local_bbox(views[n]) for n in names}
    depths = dimensions.stack_depths(specs)
    frame, title = sections._sheet(page)
    frame = [frame[0] + SHEET_MARGIN, frame[1] + SHEET_MARGIN, frame[2] - SHEET_MARGIN, frame[3] - SHEET_MARGIN]
    obstacles = [G.expand(b, 3.0) for b in keepouts if b]
    if title:
        obstacles.append(G.expand(title, 3.0))
    # PCD geometry was already created on the views. Reuse its resolved edge.
    replanned = [dict(s) for s in specs]
    for spec in replanned:
        if spec.get("refs"):
            spec.pop("make_circle", None)
    attempts = []
    for candidate in candidates:
        positions, layout_box = arrange(boxes, names, depths, {}, projection, candidate)
        if (layout_box[2] - layout_box[0] > frame[2] - frame[0]
                or layout_box[3] - layout_box[1] > frame[3] - frame[1]
                or any(2 * float(doc.getObject(name).Radius) * s > min(frame[2] - frame[0], frame[3] - frame[1])
                       for name, s in detail_scales.items())):
            attempts.append({"scale": candidate, "fits": False, "reason": "view geometry exceeds frame"})
            continue
        for dim in created:
            doc.removeObject(dim.Name)
        group.Scale = candidate
        group.X = group.Y = 0
        for name in names:
            views[name].X, views[name].Y = positions[name]
        for index, view in enumerate(extra, 1):
            # Measure each annotation block in isolation. Final packing checks
            # collisions between blocks; provisional positions must not push
            # radial leaders away from unrelated views before that packing.
            view.X, view.Y = 2000 * index, 2000
            if sections.is_section(view):
                view.Scale = candidate
        doc.recompute()
        for geom in geoms.values():
            geom.scale = G.view_scale(geom.view)
            geom.bbox = G.view_local_bbox(geom.view)
        for note in thickness_notes:
            note_box = _annotation_record(note)["bbox"]
            front_box = G.view_page_bbox(views["Front"])
            note.X = front_box[0] + (note_box[2] - note_box[0]) / 2
            note.Y = front_box[1] - 6 - (note_box[3] - note_box[1]) / 2
        created = dimensions.place(replanned, page, doc)
        doc.recompute()
        measured = measurements(created)
        if measured != expected:
            attempts.append({"scale": candidate, "fits": False, "reason": "dimension values changed",
                             "before": [v for v in expected if v not in measured][:3],
                             "after": [v for v in measured if v not in expected][:3]})
            continue
        clearance = clear_collisions(page, created, [v for v in list(views.values()) + extra if v.Name in active])
        members = {"standard": [v for v in views.values() if v.Name in active]}
        members.update({v.Name: [v] for v in extra})
        bounds = {name: footprint(items, created, extra) for name, items in members.items() if items}
        for note in thickness_notes:
            box = G.expand(_annotation_record(note)["bbox"], 3)
            if "standard" in bounds:
                old = bounds["standard"]
                bounds["standard"] = [min(old[0], box[0]), min(old[1], box[1]), max(old[2], box[2]), max(old[3], box[3])]
        bounds.update({note.Name: G.expand(_annotation_record(note)["bbox"], 3) for note in notes})
        blocks = [(name, box[2] - box[0], box[3] - box[1]) for name, box in bounds.items()]
        pieces = {}
        if "standard" in bounds:
            origin_x, origin_y = bounds["standard"][:2]
            standard_pieces = [footprint([view], created, extra) for view in members["standard"]]
            standard_pieces += [G.expand(_annotation_record(note)["bbox"], 3) for note in thickness_notes]
            pieces["standard"] = [[b[0] - origin_x, b[1] - origin_y, b[2] - origin_x, b[3] - origin_y]
                                  for b in standard_pieces]
        packed = pack_blocks(blocks, frame, obstacles, footprints=pieces)
        attempts.append({"scale": candidate, "fits": packed is not None, "clearance": clearance,
                         "blocks": [{"name": name, "width": round(w, 2), "height": round(h, 2)} for name, w, h in blocks]})
        if packed is None:
            continue
        for name, (x, y) in packed.items():
            view = group if name == "standard" else doc.getObject(name)
            view.X = float(view.X) + x - bounds[name][0]
            view.Y = float(view.Y) + y - bounds[name][1]
            if name == "standard":
                for note in thickness_notes:
                    note.X = float(note.X) + x - bounds[name][0]
                    note.Y = float(note.Y) + y - bounds[name][1]
        doc.recompute()
        return created, candidate, {"applied": True, "attempts": attempts}
    for dim in created:
        doc.removeObject(dim.Name)
    for view, x, y, old_scale, caption in original:
        view.X, view.Y, view.Scale = x, y, old_scale
        view.Caption = caption
    for view, x, y in original_offsets:
        view.X, view.Y = x, y
    for note, x, y in original_notes:
        note.X, note.Y = x, y
    doc.recompute()
    for geom in geoms.values():
        geom.scale = G.view_scale(geom.view)
        geom.bbox = G.view_local_bbox(geom.view)
    created = dimensions.place(replanned, page, doc)
    doc.recompute()
    return created, scale, {"applied": False, "attempts": attempts}


def measurements(dims):
    # Cosmetic PCD edges can be renumbered on recompute. Compare the resolved
    # model-space feature, not a view-local EdgeN that FreeCAD may remap.
    return sorted((str(d.Type), str(getattr(d, "FDRole", "")),
                   tuple(v.Name for v, _ in d.References2D),
                   repr(dimension_signature(d)), round(float(d.getRawValue()), 6)) for d in dims)


def footprint(views, dims, extra):
    """Bounding rectangle of view outlines, captions, attached lines and labels."""
    names = {v.Name for v in views}
    boxes = []
    for view in views:
        x, y = G.view_origin(view)
        relative = sections._rel_box(view)
        boxes.append([x + relative[0], y + relative[1] - (sections.CAPTION_ROOM if view.Caption else 0),
                      x + relative[2], y + relative[3]])
    for dim in dims:
        record = _dimension_record(dim)
        if record.get("view") not in names:
            continue
        if record.get("text_box"):
            boxes.append(record["text_box"])
        for x1, y1, x2, y2 in record.get("ink_lines", record.get("lines", [])):
            boxes.append([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)])
    for view in extra:
        if getattr(getattr(view, "BaseView", None), "Name", None) not in names:
            continue
        if sections.is_section(view):
            line = sections.section_record(view).get("cutting_line") or {}
            for letter in line.get("letters", []):
                x, y = letter["at"]
                boxes.append([x - 5, y - 5, x + 5, y + 5])
        elif view.TypeId == "TechDraw::DrawViewDetail":
            circle = sections.detail_record(view).get("detail_circle") or {}
            if circle:
                x, y = circle["center"]
                r = circle["radius"]
                boxes.append([x - r, y - r, x + r, y + r])
                x, y = circle["letter_at"]
                boxes.append([x - 5, y - 5, x + 5, y + 5])
    caption_room = sections.CAPTION_ROOM + 2 if any(v.Caption for v in views) else 0
    return [min(b[0] for b in boxes) - 5, min(b[1] for b in boxes) - 5 - caption_room,
            max(b[2] for b in boxes) + 5, max(b[3] for b in boxes) + 5]
