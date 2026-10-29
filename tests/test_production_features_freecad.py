"""Machining callouts must retain their physical feature and entry side."""
import json
import os
import subprocess

import pytest

from conftest import ROOT
from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd
from free_drawing_mcp.rules import inspect

pytestmark = [pytest.mark.freecad, pytest.mark.skipif(
    detect_freecadcmd() is None, reason="FreeCADCmd unavailable")]


def probe(tmp_path, body):
    result = tmp_path / "probe.json"
    script = tmp_path / "probe.py"
    script.write_text(
        "import json, sys\nimport FreeCAD as App\nimport Part\n"
        f"sys.path.insert(0, {str(ROOT / 'addon/FreeDrawingMCP')!r})\n"
        f"sys.path.insert(0, {str(ROOT / 'samples')!r})\n" + body +
        f"\nwith open({str(result)!r}, 'w', encoding='utf-8') as stream:\n    json.dump(result, stream)\n",
        encoding="utf-8")
    run = subprocess.run(detect_freecadcmd() + ["-c", f"exec(open({str(script)!r}, encoding='utf-8').read())"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                         env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert run.returncode == 0 and result.is_file(), run.stdout + run.stderr
    return json.loads(result.read_text(encoding="utf-8"))


def test_opposite_entries_are_separate_hole_requirements(tmp_path):
    result = probe(tmp_path, """
from generate_production_parts import production_parts
from fd_drawing.features import requirements
result = {}
for name, shape in production_parts().items():
    reqs, _ = requirements(shape)
    result[name] = [r for r in reqs if r['kind'] == 'dia' and r.get('feature') == 'hole']
""")
    for name in ("opposed_blind_holes", "opposed_side_holes", "opposed_counterbores", "coaxial_blind_holes"):
        holes = result[name]
        assert len(holes) == 2, (name, holes)
        assert {hole["entry_sign"] for hole in holes} == {-1, 1}
    assert all(hole["count"] == 2 for hole in result["opposed_blind_holes"])
    assert all(hole["count"] == 1 for hole in result["coaxial_blind_holes"])


def test_concentric_boss_uses_the_enclosing_cylinder_axis(tmp_path):
    result = probe(tmp_path, """
from generate_production_parts import production_parts
from fd_drawing.features import requirements
centered = production_parts()['concentric_boss']
offset = Part.makeCylinder(25, 12).fuse(Part.makeCylinder(8, 7, App.Vector(5, 3, 12)))
result = {}
for name, shape in [('centered', centered), ('offset', offset)]:
    reqs, _ = requirements(shape)
    result[name] = [r for r in reqs if r.get('source') == 'boss-center']
""")
    assert result["centered"] == []
    assert {(r["axis"], r["coord"]) for r in result["offset"]} == {("X", 5), ("Y", 3)}


def test_profile_diameter_witnesses_belong_to_the_cylinder(tmp_path):
    result = probe(tmp_path, """
from fd_drawing import dimensions, geometry, planner, page
v = App.Vector
shape = Part.makeBox(20, 20, 20, v(0, -10, -10))
shape = shape.fuse(Part.makeCylinder(5, 30, v(20, 0, 0), v(1, 0, 0)))
shape = shape.fuse(Part.makeCylinder(10, 20, v(50, 0, 0), v(1, 0, 0)))
doc = App.newDocument('Profile')
source = doc.addObject('Part::Feature', 'Source'); source.Shape = shape
sheet, _, _ = page.create_page(doc)
view = doc.addObject('TechDraw::DrawViewPart', 'Front'); sheet.addView(view)
view.Source = [source]; view.Direction = v(0, -1, 0); view.XDirection = v(1, 0, 0)
doc.recompute()
geom = dimensions.ViewGeom(view, geometry.exact_bbox(shape).Center)
req = dict(kind='dia', feature='turned', axis='X', dia=20, centers=[(60, 0, 0)],
           extent=(50, 70), text='⌀%.2w')
specs, unplaced = planner.realize([req], [geom], {})
result = {'unplaced': unplaced, 'coordinates': []}
for spec in specs:
    for ref in spec['refs']:
        point = view.getVertexByIndex(int(ref[6:])).Point
        result['coordinates'].append(geom.model_value('x', point.x))
""")
    assert not result["unplaced"]
    assert len(result["coordinates"]) == 2
    assert all(50 - 1e-6 <= coordinate <= 70 + 1e-6 for coordinate in result["coordinates"])


@pytest.mark.parametrize("projection", ["first", "third"])
def test_opposed_callouts_survive_reopening_on_the_correct_faces(tmp_path, projection):
    probe(tmp_path, f"""
from generate_production_parts import production_parts
shape = production_parts()['opposed_side_holes']
shape.exportStep({str(tmp_path / 'source.step')!r})
result = True
""")
    backend = HeadlessBackend(timeout=300)
    drawing = tmp_path / "drawing.FCStd"
    created, initial = backend.run([
        {"op": "create_drawing", "args": {"source": str(tmp_path / "source.step"),
         "output": str(drawing), "projection": projection}},
        {"op": "snapshot", "args": {"doc": str(drawing)}}])
    reopened = backend.run([{"op": "snapshot", "args": {"doc": str(drawing)}}])[0]
    assert not created["unplaced"], created
    assert [(d["text"], d["value"]) for d in initial["dimensions"]] == [
        (d["text"], d["value"]) for d in reopened["dimensions"]]
    holes = [d for d in reopened["dimensions"] if d.get("role") == "diameter:hole"]
    assert len(holes) == 2 and all(d["text"].startswith("2×") and "↧9" in d["text"] for d in holes)
    assert all(d["type"] == "Diameter" and not d["ref_hidden"] for d in holes)
    assert len({d["view"] for d in holes}) == 2
    assert any(v.get('projection_type') == 'Rear' for v in reopened['views'])
    findings = [f for f in inspect(reopened).findings
                if f.rule in ("A.8", "C.2", "E.1", "E.5", "E.10", "E.11") and f.status in ("fail", "warn")]
    assert not findings


def test_thread_class_requires_an_explicit_specification(tmp_path):
    result = probe(tmp_path, """
from fd_drawing.threads import callout
internal = dict(designation='M8×1.25', internal=True, handed='RH', through=False, length=12)
external = dict(designation='M20×2.5', internal=False, handed='LH', through=False, length=20)
result = [callout(internal, 2), callout(external), callout(dict(internal, tolerance_class='5H'))]
""")
    assert result == ["2× M8×1.25 ↧12", "M20×2.5 LH", "M8×1.25-5H ↧12"]


def test_hole_locations_from_a_circular_outline_survive_reopen(tmp_path):
    probe(tmp_path, f"""
from generate_production_parts import production_parts
production_parts()['concentric_boss'].exportStep({str(tmp_path / 'source.step')!r})
result = True
""")
    backend = HeadlessBackend(timeout=300)
    drawing = tmp_path / 'drawing.FCStd'
    created = backend.run([{'op': 'create_drawing', 'args': {
        'source': str(tmp_path / 'source.step'), 'output': str(drawing)}}])[0]
    reopened = backend.run([{'op': 'snapshot', 'args': {'doc': str(drawing)}}])[0]
    assert not created['unplaced'], created
    locations = [d for d in reopened['dimensions'] if d.get('role', '').endswith(':hole-center')]
    assert len(locations) == 2
    assert sorted(d['value'] for d in locations) == pytest.approx([25, 42])
    assert all(d['refs'] and not d['ref_hidden'] for d in locations)


def test_cosmetic_witnesses_keep_view_coordinates_across_directions_and_scales(tmp_path):
    result = probe(tmp_path, f"""
from fd_drawing import geometry, page
doc = App.newDocument('WitnessFrames')
source = doc.addObject('Part::Feature', 'Source'); source.Shape = Part.makeBox(20, 30, 10)
sheet, _, _ = page.create_page(doc)
records = []
for direction in [(1,0,0), (-1,0,0), (0,1,0), (0,-1,0), (0,0,1), (0,0,-1)]:
    view = doc.addObject('TechDraw::DrawViewPart', 'View'); sheet.addView(view)
    view.Source = [source]; view.Direction = App.Vector(*direction)
    view.XDirection = App.Vector(0,1,0) if direction[0] else App.Vector(1,0,0)
    view.ScaleType = 'Custom'
    for scale in (.5, 1, 2):
        view.Scale = scale; doc.recompute()
        ids = [geometry.ensure_cosmetic_vertex(view, p) for p in [(3,-7), (-4,6)]]
        repeated = [geometry.ensure_cosmetic_vertex(view, p) for p in [(3,-7), (-4,6)]]
        records.append({{'name': view.Name, 'ids': ids, 'repeated': repeated,
                         'points': [tuple(view.getVertexByIndex(i).Point)[:2] for i in ids]}})
path = {str(tmp_path / 'witnesses.FCStd')!r}
doc.saveAs(path); App.closeDocument(doc.Name)
doc = App.openDocument(path); doc.recompute()
for record in records:
    view = doc.getObject(record['name'])
    record['reopened'] = [tuple(view.getVertexByIndex(i).Point)[:2] for i in record['ids']]
result = records
""")
    assert len(result) == 18
    for record in result:
        assert record['ids'] == record['repeated']
        assert record['points'] == record['reopened'] == [[3, -7], [-4, 6]]
