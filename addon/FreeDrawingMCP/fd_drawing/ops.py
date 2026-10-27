"""Operation registry: the only entry points the RPC server and runner call.

Each operation takes JSON-able keyword arguments and returns a JSON-able
dict, so the GUI addon (XML-RPC) and the headless runner (freecadcmd) expose
identical behaviour.

``doc`` arguments accept an open document name or a path to an ``.FCStd``.
"""

import os

import FreeCAD as App

from . import actions as actions_mod
from . import builder, render as render_mod
from .page import find_page
from .snapshot import snapshot as take_snapshot

VERSION = "0.1.0"


def resolve_doc(doc=None, create_name=None):
    if doc:
        if str(doc).lower().endswith(".fcstd"):
            path = os.path.abspath(doc)
            for d in App.listDocuments().values():
                if d.FileName and os.path.normcase(os.path.abspath(d.FileName)) == os.path.normcase(path):
                    return d
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
            return App.openDocument(path)
        docs = App.listDocuments()
        if doc in docs:
            return docs[doc]
        for d in docs.values():
            if d.Label == doc:
                return d
        raise ValueError(f"no open document {doc!r}; open: {sorted(docs)}")
    if create_name:
        return App.newDocument(create_name)
    if App.ActiveDocument is not None:
        return App.ActiveDocument
    raise ValueError("no document given and none active")


def _save(doc, output):
    if output:
        out = os.path.abspath(output)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        doc.saveAs(out)
        return out
    return doc.FileName or None


def op_status():
    return {"addon": "FreeDrawingMCP", "version": VERSION, "freecad": ".".join(App.Version()[:3]),
            "gui": bool(App.GuiUp), "documents": sorted(App.listDocuments())}


def op_list_pages(doc=None):
    d = resolve_doc(doc)
    return {"document": d.Name, "file": d.FileName or None,
            "pages": [{"name": o.Name, "label": o.Label} for o in d.Objects if o.TypeId == "TechDraw::DrawPage"]}


def op_create_drawing(doc=None, source=None, output=None, **options):
    create_name = None
    if not doc and source and os.path.isfile(str(source)) and not str(source).lower().endswith(".fcstd"):
        create_name = builder._safe_name(os.path.splitext(os.path.basename(source))[0])
    if not doc and source and str(source).lower().endswith(".fcstd"):
        doc, source = source, None
    d = resolve_doc(doc, create_name)
    res = builder.create_drawing(d, source, **options)
    res["file"] = _save(d, output)
    return res


def op_snapshot(doc=None, page="auto", include_geometry=True):
    d = resolve_doc(doc)
    return take_snapshot(d, find_page(d, page), include_geometry=include_geometry)


def op_apply_actions(doc=None, page="auto", actions=(), save=True):
    d = resolve_doc(doc)
    p = find_page(d, page)
    results = actions_mod.apply_actions(d, p, list(actions or []))
    if save and d.FileName:
        d.save()
    return {"document": d.Name, "page": p.Name, "results": results}


def op_render(doc=None, page="auto", highlights=None, width=1600, source="auto", out_path=None):
    d = resolve_doc(doc)
    p = find_page(d, page)
    snap = take_snapshot(d, p, include_geometry=True)
    # previews use the built-in renderer: identical in GUI and console, and it
    # draws exactly the geometry the inspector measured
    svg, used = render_mod.page_svg(d, p, snap, highlights, "schematic" if source == "auto" else source)
    png = render_mod.svg_to_png(svg, width, out_path)
    return {"source": used, "png_base64": render_mod.png_base64(png), "path": out_path}


def op_export(doc=None, page="auto", format="pdf", path=None, highlights=None, source="auto"):
    d = resolve_doc(doc)
    p = find_page(d, page)
    fmt = format.lower()
    if not path:
        base = os.path.splitext(d.FileName)[0] if d.FileName else os.path.join(os.getcwd(), d.Name)
        path = f"{base}_{p.Name}.{fmt}"
    path = os.path.abspath(path)
    if fmt == "dxf":
        import TechDraw

        TechDraw.writeDXFPage(p, path)
        return {"path": path, "source": "techdraw"}
    if App.GuiUp and source in ("auto", "techdraw") and not highlights:
        import TechDrawGui

        (TechDrawGui.exportPageAsPdf if fmt == "pdf" else TechDrawGui.exportPageAsSvg)(p, path)
        return {"path": path, "source": "techdraw"}
    snap = take_snapshot(d, p, include_geometry=True)
    svg, used = render_mod.page_svg(d, p, snap, highlights, "schematic" if source == "auto" else source)
    if fmt == "svg":
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    elif fmt == "pdf":
        render_mod.svg_to_pdf(svg, path, float(p.PageWidth), float(p.PageHeight))
    elif fmt == "png":
        render_mod.svg_to_png(svg, 3000, path)
    else:
        raise ValueError("format must be pdf, svg, png or dxf")
    return {"path": path, "source": used}


def op_save(doc=None, path=None):
    d = resolve_doc(doc)
    return {"file": _save(d, path) if path else (d.save() or d.FileName)}


OPS = {
    "status": op_status,
    "list_pages": op_list_pages,
    "create_drawing": op_create_drawing,
    "snapshot": op_snapshot,
    "apply_actions": op_apply_actions,
    "render": op_render,
    "export": op_export,
    "save": op_save,
}


def op_definition_evidence(doc, path, frame_path, page="auto"):
    from .drawing_evidence import export_evidence
    document = resolve_doc(doc)
    return export_evidence(document, find_page(document, page), path, frame_path)


def op_compare_definition(source, rebuilt, frame_path, tolerance=1e-5):
    from .definition_compare import compare
    return compare(source, rebuilt, frame_path, tolerance)


def op_repair_definition(doc, gaps, page="auto"):
    from .definition_repair import repair
    document=resolve_doc(doc)
    return repair(document,find_page(document,page),gaps)


OPS.update(definition_evidence=op_definition_evidence, compare_definition=op_compare_definition, repair_definition=op_repair_definition)


def run(op, args=None):
    fn = OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown operation {op!r}; available: {sorted(OPS)}")
    return fn(**(args or {}))
