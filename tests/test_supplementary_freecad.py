"""Real geometry, saved document and dimension checks for generated cuts/details."""
import math
import pytest
from conftest import ROOT
from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd
from free_drawing_mcp.rules import inspect

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd is not installed")]


@pytest.mark.parametrize("name,folder,kind,projection", [
    ("counterbored_block", "parts", "section", "first"),
    ("counterbored_block", "parts", "section", "third"),
    ("tube", "parts", "section", "first"),
    ("micro_grooves", "complex", "detail", "first"),
    ("flange", "parts", "section", "first"),
])
def test_supplementary_views_survive_reopen(tmp_path, name, folder, kind, projection):
    output = tmp_path / f"{name}.FCStd"
    backend = HeadlessBackend(timeout=300)
    created = backend.run([{"op": "create_drawing", "args": {
        "source": str(ROOT / "samples" / folder / f"{name}.step"),
        "output": str(output), "projection": projection, "dimensioned_only": False, "allow_enlarge": False,
    }}])[0]
    snapshot = backend.run([{"op": "snapshot", "args": {"doc": str(output), "include_geometry": True}}])[0]
    selected = [v for v in snapshot["views"] if v["kind"] == kind]
    assert selected, created
    parents = {v["name"]: v for v in snapshot["views"]}
    labels = []
    for view in selected:
        assert view["base_view"] in parents
        assert view["edges_visible"] > 0
        assert all(math.isfinite(x) for x in view["bbox"])
        assert not view.get("section_error") and not view.get("detail_error")
        if kind == "section":
            assert view["hatch"] and not view.get("hatch_error")
            assert view["cutting_line"]["arrows"]
            labels.append(view["section_symbol"])
        else:
            assert view["scale"] > parents[view["base_view"]]["scale"]
            assert view["detail_circle"]
            labels.append(view["reference"])
    assert len(labels) == len(set(labels))
    findings = inspect(snapshot).findings
    assert not [f for f in findings if f.rule in ("D.1", "D.2", "E.1", "E.11") and f.status == "fail"]
    if name == "counterbored_block":
        assert not [f for f in findings if f.rule == "E.5" and f.status == "warn"]
        assert any('6.8' in d['text'] and '⌴' in d['text'] for d in snapshot['dimensions'])
    if name == "flange":
        assert {90, 120}.issubset({round(d["value"]) for d in snapshot["dimensions"] if d["value"]})
