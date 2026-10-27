"""MCP server: automatic ISO drawings in FreeCAD + guideline inspection.

Agent loop this server is designed for (each step is one cheap call):

    create_drawing(source="part.step")      -> drawing + inspection + preview
    inspect_drawing(doc)                    -> compact FAIL/WARN list, each with a fix
    autofix_drawing(doc)                    -> applies every safe fix, re-inspects
    apply_drawing_actions(doc, actions)     -> targeted edits (copy fixes from the report)
    render_drawing(doc)                     -> PNG with findings highlighted
    export_drawing(doc, format="pdf")

Backends: the FreeDrawingMCP addon inside a running FreeCAD GUI (XML-RPC,
port 9876) or headless ``freecadcmd`` on .FCStd files. ``--backend auto``
uses the GUI when reachable and falls back to headless per call.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
from mcp.types import ImageContent, TextContent

from .backends import BackendError, HeadlessBackend, RpcBackend, detect_freecadcmd
from . import workflow, definition as definition_workflow
from .rules import catalog, highlights, inspect

logger = logging.getLogger("free-drawing-mcp")
ROOT = Path(__file__).resolve().parents[2]
GUIDELINES = ROOT / "guidelines"

INSTRUCTIONS = """\
Creates ISO technical drawings from STEP/FCStd parts in FreeCAD (TechDraw) and
checks them against the project's drafting guidelines (guidelines/CHECKLIST.md).
Workflow: create_drawing -> read the report -> autofix_drawing for 'auto' fixes ->
apply_drawing_actions for the rest (copy the JSON fixes from the report; replace
<placeholders> with real values) -> inspect_drawing until RESULT is PASS or only
REVIEW items remain -> look at render_drawing for REVIEW items -> export_drawing.
Coordinates are page millimetres, origin bottom-left, Y up. Guidelines are
available as resources guideline://<name>.
"""

mcp = FastMCP("free-drawing", instructions=INSTRUCTIONS)


class State:
    backend_kind = "auto"
    rpc: RpcBackend | None = None
    headless: HeadlessBackend | None = None
    only_text = False


S = State()


def backend():
    if S.backend_kind in ("auto", "rpc") and S.rpc is not None:
        if S.backend_kind == "rpc" or S.rpc.available():
            return S.rpc
    if S.headless is None or not S.headless.available():
        raise BackendError("no FreeCAD available: start the 'Free Drawing MCP' RPC in FreeCAD, or install FreeCAD "
                           "so freecadcmd can run headless (set FREECADCMD if it is not on PATH)")
    return S.headless


def _text(s: str) -> TextContent:
    return TextContent(type="text", text=s)


def _image(b64: str) -> ImageContent:
    return ImageContent(type="image", data=b64, mimeType="image/png")


def _err(e: Exception) -> list[TextContent]:
    return [_text(f"ERROR: {e}")]


def _doc_arg(doc: str | None) -> str | None:
    if doc and doc.lower().endswith(".fcstd"):
        return str(Path(doc).expanduser().resolve())
    return doc


def _inspect_and_render(doc: str, page: str, render: bool, verbose: bool = False, width: int = 1600,
                        rules: list[str] | None = None, sections: list[str] | None = None,
                        purpose: str | None = None):
    be = backend()
    snap = be.run([{"op": "snapshot", "args": {"doc": doc, "page": page}}])[0]
    report = inspect(snap, sections=sections, rules=rules, purpose=purpose)
    out: list[Any] = [_text(report.text(verbose=verbose))]
    if render and not S.only_text:
        img = be.run([{"op": "render", "args": {"doc": doc, "page": page, "width": width,
                                                 "highlights": highlights(report)}}])[0]
        out.append(_image(img["png_base64"]))
    return report, out


# =================================================================== tools
@mcp.tool()
def drawing_status() -> list[TextContent]:
    """Which FreeCAD backend is in use and whether it responds."""
    lines = [f"backend mode: {S.backend_kind}"]
    if S.rpc is not None:
        lines.append(f"GUI addon at {S.rpc.uri}: {'reachable' if S.rpc.available() else 'not reachable'}")
    lines.append(f"headless freecadcmd: {S.headless.command if S.headless and S.headless.available() else 'not found'}")
    try:
        st = backend().run([{"op": "status"}])[0]
        lines.append(f"FreeCAD {st['freecad']} (gui={st['gui']}), addon {st['version']}, open docs: {st['documents']}")
    except Exception as e:
        lines.append(f"status call failed: {e}")
    return [_text("\n".join(lines))]


@mcp.tool()
def create_drawing(
    source: str,
    output: str | None = None,
    template: str = "A3",
    projection: Literal["first", "third"] = "first",
    scale: str = "auto",
    title: str | None = None,
    material: str | None = None,
    drawing_number: str | None = None,
    creator: str | None = None,
    title_block: dict[str, str] | None = None,
    notes: list[str] | None = None,
    views: list[str] | None = None,
    iso_color: list[int] | None = None,
    sections: bool = True,
    details: bool = True,
    dimensioned_only: bool = True,
    verify_definition: bool = True,
    section_cut_only: bool = False,
    purpose: Literal["manufacturing", "assembly", "reference", "change_notice"] = "manufacturing",
    layout_autofix: bool = True,
    inspect_result: bool = True,
    render: bool = True,
) -> list[TextContent | ImageContent]:
    """Create a complete ISO drawing from a part, then inspect it.

    Picks the front view and the smallest view set that shows every feature,
    dimensions it per guidelines/04 (baseline/ordinate from datums, hole
    callouts like '4× ⌀6 THRU', radii, chamfers, angles, PCDs), adds centre
    lines, notes and the title block. Defining views carry dimensions; a coloured
    isometric recognition view stays at the bottom right without dimensions.

    Args:
        source: STEP/IGES/BREP file, or an .FCStd file (its largest solid), or
            an object name when the GUI backend has the document open.
        output: .FCStd to save (default: '<source>_drawing.FCStd' next to the source).
        template: sheet 'A4'..'A0' or a template SVG path.
        projection: 'first' (ISO default) or 'third'.
        scale: 'auto' (largest fitting preferred scale, shared by standard views) or e.g. '1:2'.
        material / drawing_number / creator / title: title block shortcuts.
        title_block: any other ISO template fields, e.g. {"approval_person": "B. Lee"}.
        notes: general notes (default: units and ISO 8015). Supply manufacturing requirements explicitly.
        views: force views, e.g. ["Front", "Top"]; default automatic.
        iso_color: RGB 0-255 for the shaded isometric.
        sections / details: add automatic cuts and enlarged feature views where needed.
        dimensioned_only: default True; omit undimensioned defining views while
            retaining the coloured isometric at the bottom right and hidden dependency objects.
        verify_definition: rebuild supported geometry from drawing-only evidence,
            compare to the STEP, and apply at most two visible definition repairs.
        section_cut_only: project exact cut faces without geometry beyond the section plane.
        purpose: what the drawing is for; reference/assembly/change-notice drawings
            relax part-definition and specification checks (never physical layout checks).
        layout_autofix: apply safe layout-only fixes (overlaps, duplicates, centre
            lines, symbols) right after creation; never touches engineering data.
    """
    try:
        src = source
        if os.path.isfile(os.path.expanduser(source)):
            src = str(Path(source).expanduser().resolve())
        out = output
        if out is None and os.path.isfile(src):
            p = Path(src)
            out = str(p.with_name(p.stem + "_drawing.FCStd"))
        elif out:
            out = str(Path(out).expanduser().resolve())
        tb = dict(title_block or {})
        for k, v in (("part_material", material), ("drawing_number", drawing_number), ("creator", creator)):
            if v:
                tb[k] = v
        args: dict[str, Any] = {"source": src, "output": out, "template": template, "projection": projection,
                                "scale": scale, "title": title, "title_block": tb or None, "notes": notes,
                                "sections": sections, "details": details, "purpose": purpose,
                                "dimensioned_only": dimensioned_only, "section_cut_only": section_cut_only}
        if views:
            args["views"] = views
        if iso_color:
            args["iso_color"] = iso_color
        created = backend().run([{"op": "create_drawing", "args": args}])[0]
        doc = created.get("file") or created["document"]
        lines = [f"Created {created['page']} in {doc}",
                 f"part: {created['part_kind']} · views: {', '.join(created['views'])} · scale {created['scale']} · "
                 f"iso {created.get('iso_scale')} · {created['dimensions']} dimensions · {created['centerlines']} centre lines"]
        for w in created.get("warnings", []):
            lines.append(f"note: {w}")
        if layout_autofix:
            _, log, _ = workflow.autofix(backend(), doc, created["page"], rules=workflow.LAYOUT_RULES, purpose=purpose)
            lines.extend(f"layout autofix {l}" for l in log)
        if verify_definition and Path(src).suffix.lower() in ('.step','.stp') and Path(src).is_file():
            import tempfile
            verification_dir=tempfile.mkdtemp(prefix=Path(doc).stem+'-definition-',dir=str(Path(doc).parent))
            try:
                verification=definition_workflow.verify_definition(backend(),doc,src,verification_dir,max_repairs=2)
                lines.append('drawing reconstruction: '+verification['status']+'; report: '+str(Path(verification_dir)/'verification.json'))
                if verification.get('final_dimension_count') is not None:
                    lines.append('saved drawing dimensions: '+str(verification['final_dimension_count']))
            except Exception as error:
                lines.append('drawing reconstruction could not be verified: '+str(error))
        elif verify_definition:
            lines.append('drawing reconstruction not run: the original STEP file is required')
        result: list[Any] = [_text("\n".join(lines))]
        if inspect_result:
            _, more = _inspect_and_render(doc, created["page"], render, purpose=purpose)
            result.extend(more)
        return result
    except Exception as e:
        return _err(e)


@mcp.tool()
def verify_drawing_definition(doc: str, source: str, output_dir: str, max_repairs: int = 2,
                              tolerance: float = 1e-5) -> list[TextContent]:
    """Rebuild from drawing-only evidence and compare with the input STEP.

    At most max_repairs visible repairs are applied to the saved drawing.
    Use 0 for a read-only audit. Unsupported geometry is never counted as a match.
    output_dir must be new or empty. tolerance is numerical comparison precision
    in mm, not a manufacturing tolerance.
    """
    try:
        report=definition_workflow.verify_definition(backend(),doc,source,output_dir,max_repairs,tolerance)
        return [_text(json.dumps(report,indent=2,ensure_ascii=False))]
    except Exception as error:
        return _err(error)


@mcp.tool()
def inspect_drawing(
    doc: str,
    page: str = "auto",
    rules: list[str] | None = None,
    sections: list[str] | None = None,
    output: Literal["text", "json"] = "text",
    render: bool = False,
    verbose: bool = False,
    purpose: Literal["manufacturing", "assembly", "reference", "change_notice"] | None = None,
) -> list[TextContent | ImageContent]:
    """Check a drawing against every guideline checklist item (A-P) in one call.

    Returns a compact report: RESULT, score, then one line per FAIL/WARN with
    the objects involved and a numbered fix; REVIEW items need a visual look
    (set render=True for a PNG with findings boxed: red fail, orange warn).

    Args:
        doc: .FCStd path (or open document name with the GUI backend).
        page: page name, default the last page.
        rules: only these ids, e.g. ["E.10", "A.8"]. sections: e.g. ["E"].
        output: 'json' returns machine-readable findings.
        verbose: also list passed and not-applicable checks.
        purpose: override the purpose recorded on the page (default manufacturing).
    """
    try:
        doc = _doc_arg(doc)
        if output == "json":
            snap = backend().run([{"op": "snapshot", "args": {"doc": doc, "page": page}}])[0]
            rep = inspect(snap, sections=sections, rules=rules, purpose=purpose)
            return [_text(json.dumps(rep.to_dict(include_pass=verbose), ensure_ascii=False, indent=1))]
        _, out = _inspect_and_render(doc, page, render, verbose, rules=rules, sections=sections, purpose=purpose)
        return out
    except Exception as e:
        return _err(e)


@mcp.tool()
def autofix_drawing(doc: str, page: str = "auto", rules: list[str] | None = None, max_passes: int = 3,
                    render: bool = True) -> list[TextContent | ImageContent]:
    """Apply every fix marked 'auto' (safe without human input), re-inspect, repeat.

    Stops when no auto fixes remain or after max_passes. Fixes that need input
    (material, drawing number, ...) are left for apply_drawing_actions.
    """
    try:
        doc = _doc_arg(doc)
        _, log, first = workflow.autofix(backend(), doc, page, rules=rules, max_passes=max_passes)
        rep, out = _inspect_and_render(doc, page, render)
        head = [f"before: {first['result']} score {first['score']} ({first['fail']} fail, {first['warn']} warn)" if first else "",
                *log, f"after: {rep.result()} score {rep.score()}"]
        return [_text("\n".join(h for h in head if h))] + out
    except Exception as e:
        return _err(e)


@mcp.tool()
def apply_drawing_actions(doc: str, actions: list[dict[str, Any]], page: str = "auto", inspect_after: bool = True,
                          render: bool = False) -> list[TextContent | ImageContent]:
    """Apply edits, e.g. fixes copied from an inspection report.

    Actions (page mm, origin bottom-left, Y up):
      {"op":"move_dimension","name":"Dimension003","x":120,"y":210}
      {"op":"move_view","name":"ProjItem001","x":150,"y":90}      {"op":"move_group","dx":0,"dy":10}
      {"op":"set_dimension","name":..,"format_spec":"4× ⌀%.2w THRU","over_tolerance":0.1,"under_tolerance":-0.1}
      {"op":"add_dimension","view":"ProjItem","type":"DistanceX|DistanceY|Diameter|Radius|Angle","refs":["Vertex1","Vertex5"],"x":..,"y":..}
      {"op":"delete","names":[..]}   {"op":"set_title_block","fields":{"part_material":"EN AW-6082 T6"}}
      {"op":"add_note","text":[..],"x":..,"y":..}   {"op":"set_note","name":"Notes","append":["..."]}
      {"op":"set_view","name":..,"hard_hidden":true,"scale":"1:2","caption":"VIEW A"}
      {"op":"add_centerlines","view":..}  {"op":"dedupe_dimensions"}
      {"op":"set_revision","index":"B","description":"..","date":"..","by":"..","approved":".."}
    Vertex/edge names for add_dimension come from get_drawing_snapshot.
    """
    try:
        doc = _doc_arg(doc)
        res = backend().run([{"op": "apply_actions", "args": {"doc": doc, "page": page, "actions": actions}}])[0]
        lines = [("ok   " if r["ok"] else "FAIL ") + f"{r['op']}: " + (r.get("result") or r.get("error", "")) for r in res["results"]]
        out: list[Any] = [_text("\n".join(lines))]
        if inspect_after:
            _, more = _inspect_and_render(doc, page, render)
            out.extend(more)
        return out
    except Exception as e:
        return _err(e)


@mcp.tool()
def render_drawing(doc: str, page: str = "auto", highlight: bool = True, width: int = 1800,
                   source: Literal["auto", "schematic", "techdraw"] = "auto") -> list[TextContent | ImageContent]:
    """PNG of the sheet; with highlight=True findings are boxed and labelled by rule id."""
    try:
        doc = _doc_arg(doc)
        hl = []
        if highlight:
            snap = backend().run([{"op": "snapshot", "args": {"doc": doc, "page": page}}])[0]
            hl = highlights(inspect(snap), include_review=False)
        img = backend().run([{"op": "render", "args": {"doc": doc, "page": page, "width": width,
                                                        "highlights": hl, "source": source}}])[0]
        return [_text(f"rendered ({img['source']}), {len(hl)} highlighted finding(s)"), _image(img["png_base64"])]
    except Exception as e:
        return _err(e)


@mcp.tool()
def export_drawing(doc: str, format: Literal["pdf", "svg", "png", "dxf"] = "pdf", path: str | None = None,
                   page: str = "auto") -> list[TextContent]:
    """Export the sheet (PDF/SVG via TechDraw in the GUI, otherwise the built-in renderer; DXF via TechDraw)."""
    try:
        doc = _doc_arg(doc)
        args = {"doc": doc, "page": page, "format": format}
        if path:
            args["path"] = str(Path(path).expanduser().resolve())
        res = backend().run([{"op": "export", "args": args}])[0]
        return [_text(f"exported {format.upper()} to {res['path']} ({res['source']})")]
    except Exception as e:
        return _err(e)


@mcp.tool()
def get_drawing_snapshot(doc: str, page: str = "auto", include_geometry: bool = False) -> list[TextContent]:
    """Raw JSON the inspector works on: sheet, title block, views (bbox, circles, edge names),
    dimensions (text, refs, text boxes, lines), annotations, metadata. Page mm, Y up."""
    try:
        snap = backend().run([{"op": "snapshot", "args": {"doc": _doc_arg(doc), "page": page,
                                                          "include_geometry": include_geometry}}])[0]
        return [_text(json.dumps(snap, ensure_ascii=False))]
    except Exception as e:
        return _err(e)


@mcp.tool()
def list_drawing_rules() -> list[TextContent]:
    """All checklist ids the inspector reports on, grouped by section."""
    lines = []
    section = None
    for item in catalog():
        if item["section"] != section:
            section = item["section"]
            lines.append(f"## {section}")
        lines.append(f"{item['id']}  {item['check']}")
    return [_text("\n".join(lines))]


@mcp.tool()
def execute_code(code: str) -> list[TextContent]:
    """Run Python inside the FreeCAD GUI (GUI backend only) for anything the tools do not cover."""
    try:
        if S.rpc is None or not S.rpc.available():
            return [_text("ERROR: execute_code needs the FreeCAD GUI with the Free Drawing RPC running")]
        res = S.rpc.execute_code(code)
        return [_text(("ok\n" if res.get("success") else "ERROR: " + str(res.get("error")) + "\n") + (res.get("output") or ""))]
    except Exception as e:
        return _err(e)


# =============================================================== resources
@mcp.resource("guideline://index")
def guideline_index() -> str:
    """Index of the drafting guidelines."""
    return (GUIDELINES / "README.md").read_text(encoding="utf-8")


@mcp.resource("guideline://{name}")
def guideline(name: str) -> str:
    """One guideline file, e.g. guideline://04-dimensioning or guideline://CHECKLIST."""
    for p in GUIDELINES.glob("*.md"):
        if p.stem.lower() == name.lower() or p.stem.lower().startswith(name.lower()):
            return p.read_text(encoding="utf-8")
    raise ValueError(f"no guideline {name!r}; available: {sorted(p.stem for p in GUIDELINES.glob('*.md'))}")


@mcp.prompt()
def drawing_workflow(source: str) -> str:
    """Step-by-step prompt to produce a guideline-compliant drawing."""
    return (f"Create a production drawing for {source}.\n"
            "1. create_drawing(source=...). Read the report.\n"
            "2. autofix_drawing(doc=...) for every 'auto' fix.\n"
            "3. For 'needs input' fixes (material, drawing number) ask me or use known data, then apply_drawing_actions.\n"
            "4. inspect_drawing(doc=..., render=True) and check REVIEW items on the image against guideline://CHECKLIST.\n"
            "5. Repeat until RESULT is PASS, then export_drawing(format='pdf').")


def main():
    ap = argparse.ArgumentParser(description="free-drawing MCP server")
    ap.add_argument("--backend", choices=["auto", "rpc", "headless"], default=os.environ.get("FREE_DRAWING_BACKEND", "auto"))
    ap.add_argument("--host", default="localhost", help="FreeCAD RPC host (default localhost)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FREE_DRAWING_PORT", 9876)))
    ap.add_argument("--auth-token", default=None, help="RPC token (prefer FREE_DRAWING_TOKEN env var)")
    ap.add_argument("--freecadcmd", default=None, help="path to freecadcmd for the headless backend")
    ap.add_argument("--only-text-feedback", action="store_true", help="never return images")
    a = ap.parse_args()
    S.backend_kind = a.backend
    token = a.auth_token or os.environ.get("FREE_DRAWING_TOKEN") or None
    if a.backend in ("auto", "rpc"):
        S.rpc = RpcBackend(a.host, a.port, token)
    if a.backend in ("auto", "headless"):
        S.headless = HeadlessBackend([a.freecadcmd] if a.freecadcmd else detect_freecadcmd())
    S.only_text = a.only_text_feedback
    mcp.run()


if __name__ == "__main__":
    main()
