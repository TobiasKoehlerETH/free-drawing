"""MCP server wiring: tools, resources and the pure-Python tools (no FreeCAD needed)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from free_drawing_mcp import server  # noqa: E402

EXPECTED_TOOLS = {
    "drawing_status", "create_drawing", "inspect_drawing", "autofix_drawing", "apply_drawing_actions",
    "render_drawing", "export_drawing", "get_drawing_snapshot", "list_drawing_rules", "execute_code",
    "verify_drawing_definition",
}


def run(coro):
    return asyncio.run(coro)


def _attr(obj, *names):
    """mcp 1.x uses camelCase model fields, mcp 2.x snake_case."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"{type(obj).__name__} has none of {names}")


def _texts(result) -> str:
    """call_tool returns a content list, (content, structured) in later 1.x, or a CallToolResult in 2.x."""
    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, "content"):
        result = result.content
    return "\n".join(getattr(c, "text", "") for c in result)


def _resource_text(contents) -> str:
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    out = []
    for c in items:
        v = getattr(c, "content", None)
        if v is None:
            v = getattr(c, "text", c)
        out.append(v if isinstance(v, str) else v.decode("utf-8"))
    return "\n".join(out)


def test_app_object():
    assert server.mcp.name == "free-drawing"
    assert "create_drawing" in (server.INSTRUCTIONS or "")


def test_tools_listed():
    tools = run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    for t in tools:
        assert t.description, f"{t.name} has no description"


def test_create_drawing_schema():
    tool = next(t for t in run(server.mcp.list_tools()) if t.name == "create_drawing")
    schema = _attr(tool, "inputSchema", "input_schema")
    props = schema["properties"]
    assert schema["required"] == ["source"]
    assert {"source", "output", "template", "projection", "scale", "material"} <= set(props)
    assert props["dimensioned_only"]["default"] is True
    assert props["verify_definition"]["default"] is True


def test_guideline_resources_resolve():
    assert any(str(r.uri) == "guideline://index" for r in run(server.mcp.list_resources()))
    assert any(_attr(t, "uriTemplate", "uri_template") == "guideline://{name}" for t in run(server.mcp.list_resource_templates()))
    index = _resource_text(run(server.mcp.read_resource("guideline://index")))
    assert index.startswith("# ")
    checklist = _resource_text(run(server.mcp.read_resource("guideline://CHECKLIST")))
    assert "A.1 Sheet size matches declared size." in checklist
    dims = _resource_text(run(server.mcp.read_resource("guideline://04")))  # prefix match
    assert dims == (server.GUIDELINES / "04-dimensioning.md").read_text(encoding="utf-8")


def test_unknown_guideline_errors():
    with pytest.raises(Exception) as exc:
        run(server.mcp.read_resource("guideline://does-not-exist"))
    chain = f"{exc.value} {exc.value.__cause__} {exc.value.__context__}"
    assert "no guideline 'does-not-exist'" in chain and "CHECKLIST" in chain


def test_list_drawing_rules_tool():
    text = _texts(run(server.mcp.call_tool("list_drawing_rules", {})))
    assert text.startswith("## Sheet & Title Block\nA.1  ")
    assert "E.10  Dimension lines, leaders, and value text do not overlap each other." in text
    assert "P.5  " in text


def test_execute_code_without_gui_reports_error(monkeypatch):
    monkeypatch.setattr(server.S, "rpc", None)
    text = _texts(run(server.mcp.call_tool("execute_code", {"code": "print(1)"})))
    assert text.startswith("ERROR: execute_code needs the FreeCAD GUI")


def test_tools_report_missing_backend_as_text(monkeypatch):
    monkeypatch.setattr(server.S, "rpc", None)
    monkeypatch.setattr(server.S, "headless", None)
    text = _texts(run(server.mcp.call_tool("inspect_drawing", {"doc": "x.FCStd"})))
    assert text.startswith("ERROR: no FreeCAD available")


def test_prompt_listed():
    assert "drawing_workflow" in {p.name for p in run(server.mcp.list_prompts())}
