"""Do not turn missing design intent into manufacturing requirements."""
import pytest
from free_drawing_mcp.rules import inspect


@pytest.mark.parametrize("declaration,status", [
    ("ISO 2768-m", "pass"), ("ISO 2768-1-m", "pass"),
    ("ISO 2768-mK", "warn"), ("ISO 22081", "review"), ("", "fail"),
])
def test_tolerance_policy(snap, declaration, status):
    snap["title_block"]["general_tolerances"] = declaration
    snap["annotations"] = []
    findings = [f for f in inspect(snap).findings if f.rule == "F.1"]
    assert [f.status for f in findings] == [status]
    assert not any(f.auto or f.fix for f in findings)


def test_no_manufacturing_defaults_in_autofix(snap):
    snap["annotations"] = []
    snap["title_block"]["general_tolerances"] = ""
    findings = [f for f in inspect(snap).findings if f.rule in ("F.1", "H.1", "N.3")]
    assert all(not f.fix and not f.auto for f in findings)


def test_missing_supplementary_view_fails(snap):
    snap["meta"]["supplementary_views"] = {"unplaced": ["section A: no free sheet space"]}
    assert any(f.rule == "D.1" and f.status == "fail" for f in inspect(snap).findings)


def test_failed_section_geometry_fails(snap):
    snap["views"].append({"name": "SectionA", "kind": "section", "section_error": "invalid cut"})
    assert any(f.rule == "D.1" and f.status == "fail" for f in inspect(snap).findings)


@pytest.mark.parametrize('suffix,missing_depth', [(' ↧12', False), ('', True)])
def test_internal_thread_without_class_still_needs_a_depth(snap, suffix, missing_depth):
    snap['meta']['threads'] = [{'designation': 'M8×1.25', 'major_dia': 8, 'internal': True, 'handed': 'RH'}]
    snap['dimensions'][-1]['text'] = 'M8×1.25' + suffix
    findings = inspect(snap).findings
    assert any(f.rule == 'J.2' and f.status == 'warn' for f in findings)
    assert any(f.rule == 'J.3' and f.status == ('warn' if missing_depth else 'pass') for f in findings)
    assert not any(f.fix for f in findings if f.rule == 'J.2')


@pytest.mark.parametrize('projection', ['first', 'third'])
def test_rear_view_uses_freecad_far_right_position_in_both_projections(snap, projection):
    from copy import deepcopy

    front = snap['views'][1]
    rear = deepcopy(front)
    rear.update(name='Rear', projection_type='Rear', center=[250, 180], bbox=[210, 160, 290, 200])
    snap['views'] = [snap['views'][0], front, rear]
    snap['projection'] = projection
    assert not any(f.rule == 'C.2' and f.status == 'fail' for f in inspect(snap).findings)
    rear['center'] = front['center']
    assert any(f.rule == 'C.2' and f.status == 'fail' for f in inspect(snap).findings)
