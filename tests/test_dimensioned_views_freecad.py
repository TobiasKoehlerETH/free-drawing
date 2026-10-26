"""Real saved-document checks for the default dimensioned-only view policy."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT
from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd is not installed")]


@pytest.mark.parametrize("part,folder", [("micro_grooves", "complex"), ("counterbored_block", "parts")])
def test_dimensioned_only_survives_reopen(tmp_path, part, folder):
    output = tmp_path / f"{part}.FCStd"
    backend = HeadlessBackend(timeout=300)
    created, initial = backend.run([
        {"op": "create_drawing", "args": {
            "source": str(ROOT / "samples" / folder / f"{part}.step"), "output": str(output),
            "views": ["Front", "Top", "Right"], "scale": "1:1",
        }},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    reopened = backend.run([{"op": "snapshot", "args": {"doc": str(output)}}])[0]
    before = {dim["name"]: dim["value"] for dim in initial["dimensions"]}
    after = {dim["name"]: dim["value"] for dim in reopened["dimensions"]}
    assert before == after
    assert len(after) == created["dimensions"]
    attached = {dim["view"] for dim in reopened["dimensions"]}
    visible = {view["name"] for view in reopened["views"] if view["kind"] not in ("group", "pictorial")}
    assert visible == attached
    policy = reopened["meta"]["view_visibility_policy"]
    assert policy["enabled"] and not policy["removed"]
    assert {row["name"] for row in policy["hidden"]}.isdisjoint(visible)
    assert all(row["retained_in_document"] for row in policy["hidden"])
    pictorial = [view for view in reopened["views"] if view["kind"] == "pictorial"]
    assert len(pictorial) == 1 and pictorial[0]["shaded"]
    assert pictorial[0]["name"] == created["isometric"]
    assert pictorial[0]["name"] not in attached
    x0, y0, x1, y1 = pictorial[0]["bbox"]
    assert (x0 + x1) / 2 > reopened["sheet"]["width"] * 0.55
    assert y0 >= reopened["sheet"]["title_block"][3]
    assert (y0 + y1) / 2 < reopened["sheet"]["height"] * 0.6
    assert len({view["scale"] for view in reopened["views"] if view["kind"] == "ortho"}) == 1
    assert len([view for view in reopened["views"] if view["kind"] == "section"]) <= 1
    assert len([view for view in reopened["views"] if view["kind"] == "detail"]) <= 1


def test_hidden_front_anchor_keeps_dimensioned_projection(tmp_path):
    """The Front object remains necessary even when only Top carries a dimension."""
    output, evidence = tmp_path / "anchor.FCStd", tmp_path / "anchor.json"
    script = tmp_path / "create_anchor.py"
    script.write_text(f'''
import json, sys
import FreeCAD as App
import Part
sys.path.insert(0, {str(ROOT / "addon" / "FreeDrawingMCP")!r})
from fd_drawing import builder, geometry, page, snapshot
doc = App.newDocument("HiddenAnchor")
source = doc.addObject("Part::Feature", "Source")
source.Shape = Part.makeCylinder(5, 8)
sheet, _, _ = page.create_page(doc)
group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
sheet.addView(group)
group.Source = [source]
front = group.addProjection("Front")
doc.recompute()
front.Direction = App.Vector(0, -1, 0)
front.XDirection = App.Vector(1, 0, 0)
top = group.addProjection("Top")
doc.recompute()
circle = next(i for i, edge in geometry.iter_edges(top) if type(edge.Curve).__name__ == "Circle")
dim = doc.addObject("TechDraw::DrawViewDimension", "Diameter")
dim.Type = "Diameter"
dim.References2D = [(top, ("Edge" + str(circle),))]
sheet.addView(dim)
doc.recompute()
policy = builder.retain_dimensioned_views(sheet)
snapshot.set_meta(sheet, dimensioned_only=True, view_visibility_policy=policy)
doc.recompute()
doc.saveAs({str(output)!r})
with open({str(evidence)!r}, "w", encoding="utf-8") as stream:
    json.dump({{"front": front.Name, "top": top.Name, "front_visibility": front.Visibility,
               "top_visibility": top.Visibility, "group_anchor": group.Anchor.Name,
               "dimension_value": float(dim.getRawValue()), "policy": policy}}, stream)
''', encoding="utf-8")
    result = subprocess.run(detect_freecadcmd() + ["-c", f"exec(open({str(script)!r}, encoding='utf-8').read())"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(evidence.read_text(encoding="utf-8"))
    assert data["front_visibility"] is False and data["top_visibility"] is True
    assert data["group_anchor"] == data["front"]
    assert data["dimension_value"] == pytest.approx(10)
    reopened = HeadlessBackend(timeout=120).run([{"op": "snapshot", "args": {"doc": str(output)}}])[0]
    views = {view["name"] for view in reopened["views"]}
    assert data["front"] not in views and data["top"] in views
    assert reopened["dimensions"][0]["value"] == pytest.approx(10)


def test_explicit_recognition_mode_does_not_hide_views(tmp_path):
    output = tmp_path / "default.FCStd"
    created, snapshot = HeadlessBackend(timeout=120).run([
        {"op": "create_drawing", "args": {
            "source": str(ROOT / "samples" / "parts" / "counterbored_block.step"), "output": str(output),
            "auto_dimension": False, "sections": False, "details": False, "iso_style": "line", "dimensioned_only": False,
        }},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    assert created["dimensioned_only"] is False
    assert created["view_visibility_policy"] is None
    assert not snapshot["dimensions"]
    assert any(view["kind"] != "group" for view in snapshot["views"])


def test_single_chamfer_callout_does_not_require_a_detail(tmp_path):
    output = tmp_path / "chamfered.FCStd"
    created, snap = HeadlessBackend(timeout=120).run([
        {"op": "create_drawing", "args": {
            "source": str(ROOT / "samples" / "parts" / "chamfered_block.step"), "output": str(output)}},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    assert not created["details"]
    assert any(d.get("role") == "chamfer" for d in snap["dimensions"])
    assert all(d["view"] in {v["name"] for v in snap["views"] if v["kind"] == "ortho"}
               for d in snap["dimensions"] if d.get("role") == "chamfer")
