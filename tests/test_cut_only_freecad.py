"""Exact planar sections retain native geometry and measurements after saving."""

import os
import subprocess

import pytest

from free_drawing_mcp.backends import ADDON_DIR, HeadlessBackend, detect_freecadcmd

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd is not installed")]


def test_cut_only_section_omits_back_wall_and_preserves_dimension(tmp_path):
    output = tmp_path / "open_tray.FCStd"
    setup = tmp_path / "make_tray.py"
    setup.write_text(f'''
import sys
sys.path.insert(0, {str(ADDON_DIR)!r})
import FreeCAD as App, Part
from fd_drawing import page as page_mod, sections, dimensions, geometry as G
doc = App.newDocument("tray")
model = doc.addObject("Part::Feature", "Model")
model.Shape = Part.makeBox(40, 30, 12).cut(Part.makeBox(36, 26, 10, App.Vector(2, 2, 2)))
page, _, _ = page_mod.create_page(doc)
base = doc.addObject("TechDraw::DrawViewPart", "Top")
page.addView(base)
base.Source = [model]
base.Direction, base.XDirection = App.Vector(0, 0, 1), App.Vector(1, 0, 0)
base.X, base.Y = 100, 180
doc.recompute()
plan = {{"kind": "section", "letter": "A", "caption": "A-A", "base_view": base.Name,
        "normal": [-1, 0, 0], "x_direction": [0, 1, 0], "origin": [20, 15, 6],
        "half": False, "half_up": None, "axis_point": None}}
section = sections.add_section(doc, page, base, plan, 2, 240, 200)
vertices = list(G.iter_vertices(section))
ymin = min(p[1] for _, p in vertices)
bottom = sorted((p[0], i) for i, p in vertices if abs(p[1] - ymin) < 1e-6)
dimensions.make_dimension(doc, page, section, "DistanceX",
    ["Vertex" + str(bottom[0][1]), "Vertex" + str(bottom[-1][1])], "%.2w", 0, -25, role="test")
doc.recompute()
doc.saveAs({str(output)!r})
''', encoding="utf-8")
    command = detect_freecadcmd() + ["-c", f"exec(open({str(setup)!r}, encoding='utf-8').read())"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=300,
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert process.returncode == 0 and output.is_file(), process.stderr
    backend = HeadlessBackend(timeout=300)
    before, actions, after = backend.run([
        {"op": "snapshot", "args": {"doc": str(output)}},
        {"op": "apply_actions", "args": {"doc": str(output), "actions": [
            {"op": "set_view", "name": "SectionA", "cut_only": True}]}},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    assert all(result["ok"] for result in actions["results"]), actions
    old = next(v for v in before["views"] if v["kind"] == "section")
    cut = next(v for v in after["views"] if v["kind"] == "section")
    assert cut["cut_only"] and cut["type_id"] == "TechDraw::DrawViewPart"
    assert cut["center"] == old["center"] and cut["scale"] == old["scale"]
    assert cut["edges_visible"] < old["edges_visible"]
    assert cut["hatch"] and cut["native_hatch"] and not cut.get("hatch_error")
    assert [d["value"] for d in before["dimensions"]] == [30]
    assert [d["value"] for d in after["dimensions"]] == [30]
    assert after["dimensions"][0]["view"] == cut["name"]
    pdf = tmp_path / "cut_only.pdf"
    reopened, _ = backend.run([
        {"op": "snapshot", "args": {"doc": str(output)}},
        {"op": "export", "args": {"doc": str(output), "format": "pdf", "path": str(pdf)}},
    ])
    persisted = next(v for v in reopened["views"] if v["kind"] == "section")
    assert persisted["cut_only"]
    assert persisted["native_hatch"]
    assert persisted["polylines_visible"] == cut["polylines_visible"]
    assert persisted["hatch"] == cut["hatch"]
    assert reopened["dimensions"][0]["value"] == 30
    assert pdf.read_bytes().startswith(b"%PDF-")
    restored, snap = backend.run([
        {"op": "apply_actions", "args": {"doc": str(output), "actions": [
            {"op": "set_view", "name": cut["name"], "cut_only": False}]}},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    assert all(result["ok"] for result in restored["results"]), restored
    original = next(v for v in snap["views"] if v["kind"] == "section")
    assert not original["cut_only"] and original["center"] == old["center"]
    assert snap["dimensions"][0]["value"] == 30
    created, snap = backend.run([
        {"op": "apply_actions", "args": {"doc": str(output), "actions": [
            {"op": "add_section", "base": "Top", "axis": "X", "letter": "B",
             "x": 330, "y": 200, "cut_only": True}]}},
        {"op": "snapshot", "args": {"doc": str(output)}},
    ])
    assert all(result["ok"] for result in created["results"]), created
    new_cut = next(v for v in snap["views"] if v.get("section_symbol") == "B")
    assert new_cut["cut_only"] and new_cut["native_hatch"]
