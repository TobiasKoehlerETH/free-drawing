"""Saved TechDraw cosmetics retain their style without a solid model underlay."""

import xml.etree.ElementTree as ET

import pytest
from conftest import ROOT

from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd is not installed")]


def test_saved_flange_axes_and_pitch_circle_are_dash_dot(tmp_path):
    backend = HeadlessBackend(timeout=300)
    output, svg, png = (tmp_path / name for name in ("flange.FCStd", "flange.svg", "flange.png"))
    backend.run([{"op": "create_drawing", "args": {
        "source": str(ROOT / "samples" / "parts" / "flange.step"),
        "output": str(output), "views": ["Front", "Top", "Right"],
    }}])
    # A separate backend call opens the persisted FCStd in a fresh process.
    snap, _, _ = backend.run([
        {"op": "snapshot", "args": {"doc": str(output), "include_geometry": True}},
        {"op": "export", "args": {"doc": str(output), "format": "svg", "path": str(svg), "source": "schematic"}},
        {"op": "render", "args": {"doc": str(output), "out_path": str(png), "width": 1600}},
    ])
    orthos = [v for v in snap["views"] if v["kind"] == "ortho"]
    assert {v["projection_type"] for v in orthos} >= {"Front", "Top", "Right"}
    cosmetics = [c for v in orthos for c in v["cosmetic"]]
    assert any(c["geometry"] == "line" for c in cosmetics)
    pitch = [c for c in cosmetics if c["geometry"] == "circle"]
    assert pitch and all(c["radius"] > 0 for c in pitch)
    assert all(c["format"]["style"] == 4 and c["format"]["weight"] < 0.7 for c in cosmetics)
    for view in orthos:
        model_edges = {e for c in view["circles"] for e in c["edges"]}
        assert not model_edges.intersection(c["edge"] for c in view["cosmetic"])
        model_lines = {tuple(p) for p in view["polylines_visible"] + view["polylines_hidden"]}
        assert not model_lines.intersection(tuple(c["points"]) for c in view["cosmetic"])
    root = ET.parse(svg).getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    views = root.find("svg:g[@id='views']", ns)
    assert views is not None
    assert any(e.get("stroke-dasharray") == "6,1,0.5,1" for e in views.findall("svg:circle", ns))
    assert any(e.get("stroke-dasharray") == "6,1,0.5,1" for e in views.findall("svg:polyline", ns))
    dimensions = root.find("svg:g[@id='dimensions']", ns)
    assert dimensions is not None and len(dimensions)
    assert all(e.get("stroke-dasharray") in (None, "none") for e in dimensions.iter())
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
