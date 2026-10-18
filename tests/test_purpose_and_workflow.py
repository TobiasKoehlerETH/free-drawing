"""Drawing purpose as an inspection input, and the shared autofix loop."""

import copy

import pytest

from conftest import load_fixture, make_snapshot
from free_drawing_mcp import workflow
from free_drawing_mcp.rules import inspect


@pytest.fixture
def plate():
    return load_fixture("plate_hole_pattern")


def statuses(rep, rule):
    return [f.status for f in rep.findings if f.rule == rule]


def test_manufacturing_is_default_and_strict(plate):
    rep = inspect(plate)
    assert "manufacturing" in rep.sheet
    assert "fail" in statuses(rep, "K.1")


@pytest.mark.parametrize("purpose", ["reference", "change_notice", "assembly"])
def test_specification_rules_relax_but_stay_visible(plate, purpose):
    rep = inspect(plate, purpose=purpose)
    k1 = [f for f in rep.findings if f.rule == "K.1"]
    assert [f.status for f in k1] == ["na"]
    assert k1[0].message.startswith("not required for a")


def test_physical_layout_rules_apply_to_every_purpose():
    snap = make_snapshot()
    d = snap["dimensions"][1]
    d["text_box"] = list(snap["dimensions"][0]["text_box"])  # force a text overlap
    for purpose in ("manufacturing", "assembly", "reference", "change_notice"):
        assert "fail" in statuses(inspect(copy.deepcopy(snap), purpose=purpose), "E.10")


def test_reference_never_suggests_deleting_views():
    snap = make_snapshot()
    for d in snap["dimensions"]:
        if d["view"] == "TopView":
            d["view"] = "FrontView"
    rep = inspect(snap, purpose="reference")
    for f in rep.findings:
        assert not any(a.get("op") == "delete" for a in (f.fix or []) if f.rule == "C.3")


def test_change_notice_requires_revision_table(plate):
    rep = inspect(plate, purpose="change_notice")
    assert "fail" in statuses(rep, "L.2")
    plate = copy.deepcopy(plate)
    plate["meta"]["revisions"] = [{"index": "B", "description": "x", "date": "d", "by": "a", "approved": "b"}]
    plate["title_block"]["revision_index"] = "B"
    assert "fail" not in statuses(inspect(plate, purpose="change_notice"), "L.2")


def test_purpose_recorded_on_page_is_used(plate):
    plate = copy.deepcopy(plate)
    plate["meta"]["purpose"] = "reference"
    assert "reference" in inspect(plate).sheet


def test_unknown_purpose_rejected(plate):
    with pytest.raises(ValueError):
        inspect(plate, purpose="poster")


def test_page_without_views_cannot_pass():
    snap = make_snapshot()
    snap["views"] = []
    rep = inspect(snap, purpose="reference")
    assert statuses(rep, "C.1") == ["fail"]
    assert rep.result() == "FAIL"


class FakeBackend:
    """Applies move_dimension fixes to an in-memory snapshot."""

    def __init__(self, snap):
        self.snap = snap
        self.calls = 0

    def run(self, steps):
        out = []
        for st in steps:
            if st["op"] == "snapshot":
                out.append(copy.deepcopy(self.snap))
            elif st["op"] == "apply_actions":
                self.calls += 1
                res = []
                for a in st["args"]["actions"]:
                    if a["op"] == "move_dimension":
                        d = next(d for d in self.snap["dimensions"] if d["name"] == a["name"])
                        tb = d["text_box"]
                        w, h = tb[2] - tb[0], tb[3] - tb[1]
                        d["text_box"] = [a["x"] - w / 2, a["y"] - h / 2, a["x"] + w / 2, a["y"] + h / 2]
                        d["text_center"] = [a["x"], a["y"]]
                    res.append({"op": a["op"], "ok": True, "result": "done"})
                out.append({"results": res})
        return out


def test_autofix_resolves_overlap_and_stops():
    snap = make_snapshot()
    snap["dimensions"][1]["text_box"] = list(snap["dimensions"][0]["text_box"])
    be = FakeBackend(snap)
    rep, log, first = workflow.autofix(be, "x.FCStd", rules=workflow.LAYOUT_RULES)
    assert first["fail"] >= 1
    assert "fail" not in statuses(rep, "E.10")
    assert be.calls >= 1 and log


def test_autofix_never_repeats_identical_actions():
    snap = make_snapshot()
    snap["dimensions"][1]["text_box"] = list(snap["dimensions"][0]["text_box"])

    class Stubborn(FakeBackend):  # pretends to apply but changes nothing
        def run(self, steps):
            if steps[0]["op"] == "apply_actions":
                self.calls += 1
                return [{"results": [{"op": a["op"], "ok": True, "result": "noop"} for a in steps[0]["args"]["actions"]]}]
            return super().run(steps)

    be = Stubborn(snap)
    workflow.autofix(be, "x.FCStd", rules=workflow.LAYOUT_RULES, max_passes=5)
    assert be.calls == 1
