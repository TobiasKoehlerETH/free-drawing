"""Report formatting, filtering, catalog and coverage on real fixtures (pure Python)."""

from __future__ import annotations

import json
import re
import time

import pytest
from conftest import REAL_FIXTURES, ROOT, dim, load_fixture, view

from free_drawing_mcp.rules import Finding, Report, catalog, highlights, inspect

# section letter -> number of checklist items in guidelines/CHECKLIST.md
CHECKLIST_COUNTS = {"A": 8, "B": 5, "C": 8, "D": 5, "E": 11, "F": 5, "G": 5, "H": 5, "I": 3, "J": 5,
                    "K": 4, "L": 3, "M": 3, "N": 4, "O": 4, "P": 5}
ALL_IDS = [f"{s}.{i}" for s, n in CHECKLIST_COUNTS.items() for i in range(1, n + 1)]


def failing(snap):
    """Synthetic snapshot with one auto fix (B.3), one manual fix (K.1) and one warn (N.3)."""
    view(snap, "Front")["circles"][0]["center_mark"] = False
    snap["title_block"]["part_material"] = ""
    notes = snap["annotations"][0]["text"]
    notes[notes.index("BREAK ALL SHARP EDGES 0.2-0.5")] = "DEBURR"
    return snap


# ------------------------------------------------------------------ text()
def test_text_pass_format(snap):
    lines = inspect(snap).text().splitlines()
    assert lines[0].startswith("DRAWING CHECK  synthetic.FCStd > Page  (A3 landscape")
    assert re.match(r"^RESULT PASS · score 100/100 · 0 fail · 0 warn · \d+ review · \d+ pass · \d+ n/a$", lines[1])
    assert not any(l.startswith("FIXES") for l in lines)


def test_text_fail_format_numbered_fixes(snap):
    rep = inspect(failing(snap))
    text = rep.text()
    lines = text.splitlines()
    assert re.match(r"^RESULT FAIL · score \d+/100 · 2 fail · 1 warn · ", lines[1])
    # fails sorted before warns, each with its fix number and auto / needs-input tag
    body = [l for l in lines[2:] if re.match(r"^(FAIL|WARN) ", l)]
    assert body[0].startswith("FAIL   B.3") and body[0].endswith("-> fix 1 (auto)")
    assert body[1].startswith("FAIL   K.1") and body[1].endswith("-> fix 2 (needs input)")
    assert body[2].startswith("WARN   N.3") and "-> fix" not in body[2]
    assert "[Front]" in body[0]
    i = lines.index(next(l for l in lines if l.startswith("FIXES")))
    fixes = lines[i + 1:]
    assert [int(l.split()[0]) for l in fixes] == [1, 2]
    assert json.loads(fixes[0].split(None, 1)[1]) == {"op": "add_centerlines", "view": "Front"}
    assert json.loads(fixes[1].split(None, 1)[1])["op"] == "set_title_block"
    assert "REVIEW (visual" in text


def test_text_verbose_lists_pass_and_na(snap):
    short, verbose = inspect(snap).text(), inspect(snap).text(verbose=True)
    assert "PASS   A.1" not in short and "PASS   A.1" in verbose
    assert "N/A    G.1" in verbose


def test_text_hint_line(snap):
    snap["sheet"]["projection_symbol"] = None
    assert "hint: use an ISO template" in inspect(snap).text()


# ------------------------------------------------------------ result/score
def test_counts_summary_score(snap):
    rep = inspect(failing(snap))
    c = rep.counts()
    assert (c["fail"], c["warn"]) == (2, 1)
    assert sum(c.values()) == len(rep.findings)
    s = rep.summary()
    assert s["result"] == "FAIL" and s["auto_fixes"] == 1
    expected = round(100 * (c["pass"] + 0.5 * c["warn"]) / (c["pass"] + c["warn"] + c["fail"]))
    assert rep.score() == s["score"] == expected


def test_result_warn_only(snap):
    notes = snap["annotations"][0]["text"]
    notes[notes.index("BREAK ALL SHARP EDGES 0.2-0.5")] = "DEBURR"
    assert inspect(snap).result() == "WARN"


def test_empty_report_scores_100():
    rep = Report(page="P", document=None, sheet="-", findings=[Finding("A.1", "na", "x")])
    assert rep.score() == 100 and rep.result() == "PASS"


def test_to_dict(snap):
    rep = inspect(failing(snap))
    d = rep.to_dict()
    assert {f["rule"] for f in d["findings"]} >= {"B.3", "K.1", "N.3"}
    assert all(f["status"] not in ("pass", "na") for f in d["findings"])
    assert len(rep.to_dict(include_pass=True)["findings"]) == len(rep.findings)
    json.dumps(d, ensure_ascii=False)  # serialisable


# ----------------------------------------------------------- auto_actions
def test_auto_actions_only_auto_fixes(snap):
    rep = inspect(failing(snap))
    acts = rep.auto_actions()
    assert {"op": "add_centerlines", "view": "Front"} in acts
    assert not any(a["op"] == "set_note" for a in acts)  # edge allowance needs design input
    assert not any(a["op"] == "set_title_block" for a in acts)  # K.1 needs input
    assert rep.auto_actions(["B.3"]) == [{"op": "add_centerlines", "view": "Front"}]
    assert rep.auto_actions(["K.1"]) == []


# -------------------------------------------------------------- highlights
def test_highlights(snap):
    snap = failing(snap)
    rep = inspect(snap)
    hl = highlights(rep)
    labels = {h["label"] for h in hl}
    assert {"B.3", "K.1"} <= labels
    for h in hl:
        assert h["severity"] in ("fail", "warn") and len(h["bbox"]) == 4
    b3 = next(h for h in hl if h["label"] == "B.3")
    assert b3 == {"bbox": [94.0, 174.0, 106.0, 186.0], "label": "B.3", "severity": "fail"}


def test_highlights_review_optional():
    rep = Report(page="P", document=None, sheet="-", findings=[
        Finding("C.1", "review", "look", bbox=[0, 0, 1, 1]), Finding("A.1", "pass", "ok", bbox=[0, 0, 1, 1])])
    assert highlights(rep) == []
    assert highlights(rep, include_review=True) == [{"bbox": [0, 0, 1, 1], "label": "C.1", "severity": "review"}]


# ---------------------------------------------------------------- filters
def test_inspect_rules_filter(snap):
    rep = inspect(failing(snap), rules=["e.10", "B.3"])
    assert {f.rule for f in rep.findings} == {"E.10", "B.3"}


def test_inspect_sections_filter(snap):
    rep = inspect(snap, sections=["a", "K"])
    assert {f.rule.split(".")[0] for f in rep.findings} == {"A", "K"}
    assert {f.rule for f in rep.findings if f.rule.startswith("A")} == {f"A.{i}" for i in range(1, 9)}


def test_inspect_sections_and_rules_combined(snap):
    assert inspect(snap, sections=["A"], rules=["E.1"]).findings == []


def test_report_metadata(snap):
    snap["meta"]["unplaced"] = ["slot"]
    rep = inspect(snap)
    assert rep.page == "Page" and rep.document == "synthetic.FCStd"
    assert rep.sheet == "A3 landscape · first-angle · 1 : 1 · manufacturing"
    assert rep.unplaced == ["slot"]


# ---------------------------------------------------------------- catalog
def test_catalog_has_every_checklist_id():
    cat = catalog()
    ids = [c["id"] for c in cat]
    assert ids == ALL_IDS
    assert all(c["section"] and c["check"] for c in cat)
    assert cat[0] == {"id": "A.1", "section": "Sheet & Title Block", "check": "Sheet size matches declared size."}
    assert next(c for c in cat if c["id"] == "P.5")["section"] == "Project Overrides"


def test_catalog_matches_checklist_file():
    text = (ROOT / "guidelines" / "CHECKLIST.md").read_text(encoding="utf-8")
    assert [c["id"] for c in catalog()] == re.findall(r"^- \[ \] ([A-P]\.\d+) ", text, re.M)


# ------------------------------------------------------- real fixtures
@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_complete_coverage_on_real_fixture(name):
    rep = inspect(load_fixture(name))
    reported = {f.rule for f in rep.findings}
    assert not [f for f in rep.findings if f.rule.endswith(".*")], "a rule section crashed"
    missing = [i for i in ALL_IDS if i not in reported]
    assert not missing, f"checklist ids without a finding: {missing}"
    assert all(f.status in ("fail", "warn", "review", "pass", "na") for f in rep.findings)


def test_complete_coverage_on_synthetic(snap):
    assert set(ALL_IDS) <= {f.rule for f in inspect(snap).findings}


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_real_fixture_inspection_is_fast(name):
    snap = load_fixture(name)
    inspect(snap)  # warm up imports / regex caches
    t0 = time.perf_counter()
    rep = inspect(snap)
    rep.text()
    rep.auto_actions()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.2, f"inspection took {elapsed * 1000:.0f} ms"


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_real_fixture_expected_findings(name):
    rep = inspect(load_fixture(name))
    k1 = [f for f in rep.findings if f.rule == "K.1"]
    assert [f.status for f in k1] == ["fail"]  # generated drawings leave the material to the user
    assert not [f for f in rep.findings if f.rule in ("E.1", "E.11", "A.8", "C.2", "C.6", "C.7", "P.5")
                and f.status == "fail"]
    assert rep.sheet == "A3 landscape · first-angle · 1 : 1 · manufacturing"


def test_real_fixture_material_set_passes_k1():
    snap = load_fixture("flange")
    snap["title_block"]["part_material"] = "EN AW-6082 T6"
    rep = inspect(snap)
    assert all(f.status == "pass" for f in rep.findings if f.rule == "K.1")
    assert rep.result() == "WARN"  # legacy mK tolerances remain visible for review
