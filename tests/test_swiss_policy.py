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
