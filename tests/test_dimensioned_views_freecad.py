"""Real saved-document checks for the default dimensioned-only view policy."""
import json
import os
import subprocess
import re
from pathlib import Path

import pytest

from conftest import ROOT
from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd
from free_drawing_mcp.definition import verify_definition

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd is not installed")]


@pytest.mark.parametrize('part,thickness', [('plate_hole_pattern',12),('flange',15),('slotted_plate',6),('large_frame',50),('sheet_tray',1.5)])
def test_thickness_is_a_native_dimension_on_a_drawing_view(tmp_path,part,thickness):
    output=tmp_path/(part+'.FCStd')
    backend=HeadlessBackend(timeout=300)
    backend.run([{'op':'create_drawing','args':{'source':str(ROOT/'samples/parts'/(part+'.step')),'output':str(output)}}])
    snap=backend.run([{'op':'snapshot','args':{'doc':str(output)}}])[0]
    standard={v['name']:v for v in snap['views'] if v['kind']=='ortho'}
    assert len(standard)>=2
    assert len({v['scale'] for v in standard.values()})==1
    permitted=set(standard)
    if part=='sheet_tray':permitted.update(v['name'] for v in snap['views'] if v['kind']=='section')
    dims=[d for d in snap['dimensions'] if d['type'] in ('DistanceX','DistanceY')
          and abs(d['value']-thickness)<1e-6 and d['view'] in permitted]
    if part=='sheet_tray':dims=[d for d in dims if d.get('role','').endswith(':thickness')]
    assert dims
    assert all(d['refs'] and not d['ref_hidden'] for d in dims)
    assert not any(re.fullmatch(r't\s*=?\s*\d+(?:\.\d+)?',line,re.I)
                   for a in snap['annotations'] for line in a.get('text',[]))
    assert any(v['kind']=='pictorial' and v['shaded'] for v in snap['views'])
    if part!='sheet_tray':
        report=verify_definition(backend,output,ROOT/'samples/parts'/(part+'.step'),tmp_path/'definition')
        assert report['status']=='match_within_scope',report
        assert report['source_unchanged']


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


def test_tiny_part_isometric_is_large_enough_to_recognize(tmp_path):
    output=tmp_path/'tiny.FCStd'
    snap=HeadlessBackend(timeout=120).run([
        {'op':'create_drawing','args':{'source':str(ROOT/'samples/parts/tiny_pin.step'),'output':str(output)}},
        {'op':'snapshot','args':{'doc':str(output)}},
    ])[1]
    iso=next(v for v in snap['views'] if v['kind']=='pictorial')
    assert iso['shaded'] and snap['meta']['iso_readability_enlarged']
    box=iso['bbox']
    assert max(box[2]-box[0],box[3]-box[1])>=35
    assert box[1]>=snap['sheet']['title_block'][3]
