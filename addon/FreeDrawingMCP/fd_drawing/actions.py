"""Small, composable edits an agent applies after inspection.

Every inspection finding carries a ready ``{"op": ..., ...}`` dict; passing a
list of them to :func:`apply_actions` performs the edits in one recompute.
All coordinates are page mm (origin bottom-left, Y up).
"""

import FreeCAD as App

from . import analysis, builder, dimensions, page as page_mod, sections
from . import geometry as G
from .snapshot import get_meta, page_views, set_meta


def _obj(doc, name):
    obj = doc.getObject(name) or next((o for o in doc.Objects if o.Label == name), None)
    if obj is None:
        raise ValueError(f"no object {name!r}")
    return obj


def move_view(doc, page, name, x, y):
    v = _obj(doc, name)
    if v.TypeId == "TechDraw::DrawProjGroup":
        v.X, v.Y = x, y
    else:
        G.set_view_origin(v, x, y)
    return f"moved view {v.Name} to ({x:.1f}, {y:.1f})"


def move_group(doc, page, dx=0.0, dy=0.0, name=None):
    grp = _obj(doc, name) if name else next((o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"), None)
    if grp is None:
        raise ValueError("page has no projection group")
    grp.X = float(grp.X) + dx
    grp.Y = float(grp.Y) + dy
    return f"shifted {grp.Name} by ({dx:.1f}, {dy:.1f})"


def move_dimension(doc, page, name, x, y):
    d = _obj(doc, name)
    view = d.References2D[0][0]
    ox, oy = G.view_origin(view)
    d.X, d.Y = x - ox, y - oy
    return f"moved {d.Name} label to ({x:.1f}, {y:.1f})"


def set_dimension(doc, page, name, format_spec=None, arbitrary=None, over_tolerance=None,
                  under_tolerance=None, equal_tolerance=None, theoretical_exact=None, show_units=None):
    d = _obj(doc, name)
    if format_spec is not None:
        d.FormatSpec = format_spec
    if arbitrary is not None:
        d.Arbitrary = bool(arbitrary)
    if equal_tolerance is not None:
        d.EqualTolerance = bool(equal_tolerance)
    if over_tolerance is not None:
        d.OverTolerance = float(over_tolerance)
    if under_tolerance is not None:
        d.UnderTolerance = float(under_tolerance)
    if theoretical_exact is not None:
        d.TheoreticalExact = bool(theoretical_exact)
    if show_units is not None:
        d.ShowUnits = bool(show_units)
    return f"updated {d.Name}"


def delete(doc, page, names):
    done = []
    for n in names if isinstance(names, list) else [names]:
        o = doc.getObject(n)
        if o is not None:
            doc.removeObject(o.Name)
            done.append(n)
    return f"deleted {done}"


def set_title_block(doc, page, fields):
    res = page_mod.set_title_block(page, fields)
    if res["unknown_fields"]:
        return f"updated {res['updated']}; unknown fields {res['unknown_fields']} (available: {res['available_fields']})"
    return f"updated title block {res['updated']}"


def add_dimension(doc, page, view, type, refs, x=None, y=None, format_spec=None):
    v = _obj(doc, view)
    ox, oy = G.view_origin(v)
    d = dimensions.make_dimension(doc, page, v, type, refs, format_spec,
                                  None if x is None else x - ox, None if y is None else y - oy, role="manual")
    return f"added {d.Name}"


def add_note(doc, page, text, x, y, name="Note", size=builder.NOTE_SIZE):
    n = builder.add_note(doc, page, text if isinstance(text, list) else [text], x, y, name=name, size=size)
    return f"added note {n.Name}"


def set_note(doc, page, name, text=None, x=None, y=None, append=None):
    n = _obj(doc, name)
    if text is not None:
        n.Text = text if isinstance(text, list) else [text]
    if append:
        n.Text = list(n.Text) + (append if isinstance(append, list) else [append])
    if x is not None:
        n.X = x
    if y is not None:
        n.Y = y
    return f"updated note {n.Name}"


def set_view(doc, page, name, hard_hidden=None, scale=None, caption=None, direction=None, x_direction=None):
    v = _obj(doc, name)
    if hard_hidden is not None:
        v.HardHidden = bool(hard_hidden)
    if scale is not None:
        target = G.proj_group_of(v) or v
        target.ScaleType = "Custom"
        target.Scale = float(builder._parse_scale(scale))
    if caption is not None:
        v.Caption = caption
    if direction is not None:
        v.Direction = App.Vector(*direction)
    if x_direction is not None:
        v.XDirection = App.Vector(*x_direction)
    return f"updated view {v.Name}"


def add_centerlines(doc, page, view=None):
    names = [view] if view else [o.Name for o in page_views(page)
                                 if G.is_type(o, "TechDraw::DrawViewPart") and G.is_principal(o.Direction)]
    total = 0
    for n in names:
        v = _obj(doc, n)
        shape = v.Source[0].Shape if v.Source else None
        if shape is None:
            continue
        total += builder.add_centerlines(v, shape, G.view_scale(v))
    return f"added {total} centre lines"


def dedupe_dimensions(doc, page):
    removed = dimensions.dedupe(page, doc)
    set_meta(page, dedupe_ran=True)
    return f"removed duplicates {removed}"


def set_revision(doc, page, index, description="", date="", by="", approved=""):
    meta = get_meta(page)
    revs = list(meta.get("revisions", []))
    revs.append({"index": index, "description": description, "date": date, "by": by, "approved": approved})
    set_meta(page, revisions=revs)
    page_mod.set_title_block(page, {"revision_index": index})
    # revision table in the upper-right corner, newest row nearest the title block [11]
    lines = ["REV | DESCRIPTION | DATE | BY | APPR"] + [
        f"{r['index']} | {r['description']} | {r['date']} | {r['by']} | {r['approved']}" for r in revs]
    tbl = doc.getObject("RevisionTable")
    if tbl is None:
        tbl = doc.addObject("TechDraw::DrawViewAnnotation", "RevisionTable")
        page.addView(tbl)
    tbl.Text = lines
    tbl.TextSize = 3.0
    w, h = builder.note_size(lines, 3.0)
    W, H = float(page.PageWidth), float(page.PageHeight)
    tbl.X = W - 16 - w / 2
    tbl.Y = H - 16 - h / 2
    return f"revision {index} recorded"


def _base_geom(doc, base):
    """(view object, ViewGeom) for a base view given by object name/label or role ('Front', 'Top', ...)."""
    v = doc.getObject(base) or next((o for o in doc.Objects if o.Label == base), None)
    if v is None:
        v = next((o for o in page_views_all(doc) if str(getattr(o, "Type", "")) == base), None)
    if v is None:
        raise ValueError(f"no view {base!r}")
    key = str(getattr(v, "Type", "") or v.Name) if G.proj_group_of(v) is not None else v.Name
    return v, dimensions.ViewGeom(v, sections.view_center3d(v), key)


def page_views_all(doc):
    return [o for o in doc.Objects if G.is_type(o, "TechDraw::DrawViewPart")]


def _place(doc, page, view, near_view, side=None):
    want = {"rel_box": sections._rel_box(view), "near": G.view_page_bbox(near_view), "side": side}
    pos = sections.place_extra_views(page, [want])[0]
    if pos is None:
        pos = (float(page.PageWidth) / 2, float(page.PageHeight) / 2)
    view.X, view.Y = pos
    return pos


def add_section(doc, page, base, axis=None, origin=None, letter=None, half=False, x=None, y=None, scale=None):
    """Section view cut on ``base`` [03].

    ``axis``: model axis normal to the cutting plane ('X'|'Y'|'Z'), or omitted to
    let :func:`sections.decide_sections` choose for the base view. ``origin``:
    a point [x, y, z] on the plane or the plane coordinate along ``axis``.
    The section looks like the existing view with the same direction (if
    any); ``x``/``y`` page mm, else placed in free space.
    """
    v, vg = _base_geom(doc, base)
    shape = sections._source_shape(v)
    letter = letter or sections.next_letters(sections.used_letters(page), 1)[0]
    s = float(scale) if scale is not None else G.view_scale(v)
    if axis is None:
        info = analysis.classify(shape)
        geoms = [vg]
        for o in page_views_all(doc):
            if o is not v and G.proj_group_of(o) is not None and G.is_principal(o.Direction):
                geoms.append(dimensions.ViewGeom(o, sections.view_center3d(o), str(o.Type)))
        plans = [p for p in sections.decide_sections(shape, geoms, (), (), info, 3, sections.used_letters(page))
                 if p["base_view"] == v.Name]
        if not plans:
            raise ValueError(f"no internal feature needs a section on {base!r}; pass axis= and origin=")
        plan = plans[0]
        plan["letter"], plan["caption"] = letter, f"{letter}–{letter}"
        if half is not None and not half:
            plan["half"] = plan["half"] and bool(half)
    else:
        axis = str(axis).upper()
        nvec = App.Vector(*[1.0 if a == axis else 0.0 for a in "XYZ"])
        if abs(nvec.dot(App.Vector(v.Direction))) > 0.5:
            raise ValueError(f"cutting plane normal {axis} is the view direction of {base!r}; it must lie in that view")
        bb = G.exact_bbox(shape)
        o = App.Vector(bb.Center)
        if isinstance(origin, (list, tuple)) and len(origin) == 3:
            o["XYZ".index(axis)] = float(origin["XYZ".index(axis)])
        elif origin is not None:
            o["XYZ".index(axis)] = float(origin)
        like = next((g for g in page_views_all(doc) if G.proj_group_of(g) is not None
                     and abs(abs(App.Vector(g.Direction).dot(nvec)) - 1) < 1e-6), None)
        if like is not None:
            D, X = App.Vector(like.Direction), App.Vector(like.XDirection)
        else:
            bx = App.Vector(v.XDirection)
            bu = G.up_vector(App.Vector(v.Direction), bx)
            if abs(nvec.dot(bx)) > 0.5:
                D = bx * -1.0
                X = bu.cross(D)
            else:
                D, X = bu, bx
        plan = {"kind": "section", "letter": letter, "caption": f"{letter}–{letter}", "base": vg.name,
                "base_view": v.Name, "like": str(like.Type) if like is not None else None,
                "normal": [D.x, D.y, D.z], "x_direction": [X.x, X.y, X.z], "origin": [o.x, o.y, o.z],
                "plane_axis": axis, "coord": o["XYZ".index(axis)], "half": False, "half_up": None,
                "axis_point": None, "side": "right", "features": ["manual"], "reasons": ["manual"]}
        if half:
            # half section about the part axis lying in the plane (through the bbox centre)
            up = D.cross(X)
            up.normalize()
            plan.update(half=True, half_up=[up.x, up.y, up.z], axis_point=[o.x, o.y, o.z])
    sec = sections.add_section(doc, page, v, plan, s, x, y)
    if x is None or y is None:
        _place(doc, page, sec, v, plan.get("side"))
    return f"added section {sec.Name} ({plan['caption']}) on {v.Name}"


def add_detail(doc, page, base, x, y, radius=None, scale=None, letter=None, at=None):
    """Enlarged detail of ``base`` around page point (x, y) [03 rule 5].

    ``radius``: page mm on the base view (default 8). ``scale``: absolute
    detail scale (2, 5, 10 ...; default the smallest preferred enlargement
    >= 2x the base scale that keeps the detail <= ~70 mm). ``at``: optional
    [x, y] page position of the detail view itself.
    """
    v, vg = _base_geom(doc, base)
    sb = G.view_scale(v)
    ox, oy = G.view_origin(v)
    r_model = (float(radius) if radius else 8.0) / sb
    if scale is None:
        cands = [c for c in ([1.0] if sb < 1 else []) + sections.DETAIL_SCALES if c >= 2 * sb - 1e-9]
        fits = [c for c in cands if 2 * r_model * c <= sections.DETAIL_MAX_PAPER] or cands[:1]
        scale = fits[-1] if len(fits) == 1 else fits[min(1, len(fits) - 1)] if 2 * r_model * fits[0] < 25 else fits[0]
    else:
        scale = float(builder._parse_scale(scale))
    letter = letter or sections.next_letters(sections.used_letters(page), 1)[0]
    plan = {"kind": "detail", "letter": letter, "caption": f"{letter} ({sections.ratio_text(scale)})",
            "base": vg.name, "base_view": v.Name, "anchor": [(x - ox) / sb, (y - oy) / sb], "radius": r_model,
            "scale": scale, "items": ["manual"], "reasons": ["manual"]}
    det = sections.add_detail(doc, page, v, plan, *(at or (None, None)))
    if not at:
        _place(doc, page, det, v)
    return f"added detail {det.Name} ({plan['caption']}) on {v.Name}"


def set_meta_action(doc, page, **fields):
    set_meta(page, **fields)
    return f"meta updated {sorted(fields)}"


ACTIONS = {
    "move_view": move_view,
    "move_group": move_group,
    "move_dimension": move_dimension,
    "set_dimension": set_dimension,
    "delete": delete,
    "set_title_block": set_title_block,
    "add_dimension": add_dimension,
    "add_note": add_note,
    "set_note": set_note,
    "set_view": set_view,
    "add_centerlines": add_centerlines,
    "dedupe_dimensions": dedupe_dimensions,
    "set_revision": set_revision,
    "set_meta": set_meta_action,
    "add_section": add_section,
    "add_detail": add_detail,
}


def apply_actions(doc, page, actions):
    results = []
    for act in actions:
        act = dict(act)
        op = act.pop("op", None)
        act.pop("auto", None)
        act.pop("rule", None)
        fn = ACTIONS.get(op)
        if fn is None:
            results.append({"op": op, "ok": False, "error": f"unknown action; use one of {sorted(ACTIONS)}"})
            continue
        try:
            results.append({"op": op, "ok": True, "result": fn(doc, page, **act)})
        except Exception as e:
            results.append({"op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
    doc.recompute()
    return results
