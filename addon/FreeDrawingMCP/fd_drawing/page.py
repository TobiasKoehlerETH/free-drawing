"""Pages, templates and title blocks."""

import datetime

from . import templates
from .standards import DEFAULT_GENERAL_TOLERANCE


def find_page(doc, name=None):
    pages = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"]
    if not pages:
        raise ValueError(f"document {doc.Name!r} has no TechDraw page")
    if not name or name == "auto":
        return pages[-1]
    for p in pages:
        if p.Name == name or p.Label == name:
            return p
    raise ValueError(f"no page {name!r} in {doc.Name!r}; pages: {[p.Name for p in pages]}")


def create_page(doc, name="Page", template="A3", orientation="landscape"):
    path = templates.resolve_template(template, orientation)
    page = doc.addObject("TechDraw::DrawPage", name)
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", name + "Template")
    tpl.Template = path
    page.Template = tpl
    return page, tpl, path


def scale_text(scale):
    if scale >= 1:
        v = round(scale, 3)
        return f"{int(v) if v == int(v) else v} : 1"
    inv = round(1.0 / scale, 3)
    return f"1 : {int(inv) if inv == int(inv) else inv}"


def default_title_fields(doc, scale, title=None):
    today = datetime.date.today().strftime("%Y-%m-%d")
    return {
        "title": title or doc.Label,
        "scale": scale_text(scale),
        "general_tolerances": DEFAULT_GENERAL_TOLERANCE,
        "date_of_issue": today,
        "sheet_number": "1 / 1",
        "revision_index": "A",
        "document_type": "Part drawing",
        "document_status": "In preparation",
        # template sample values are cleared rather than left as fake data
        "supplementary_title_1": "",
        "supplementary_title_2": "",
        "legal_owner_1": "",
        "responsible_department": "",
        "approval_person": "",
        "creator": "",
        "drawing_number": "",
        "part_material": "",
    }


def set_title_block(page, fields):
    tpl = page.Template
    if tpl is None:
        raise ValueError("page has no template")
    texts = dict(tpl.EditableTexts)
    unknown = [k for k in fields if k not in texts]
    for k, v in fields.items():
        if k in texts:
            texts[k] = "" if v is None else str(v)
    tpl.EditableTexts = texts
    return {"updated": sorted(k for k in fields if k in texts), "unknown_fields": unknown,
            "available_fields": sorted(texts)}
