"""Targeted PASS / FAIL tests per rule on the synthetic snapshot (pure Python)."""

from __future__ import annotations

import pytest
from conftest import by_rule, circle, dim, load_fixture, make_snapshot, view

from free_drawing_mcp.rules import checks, geom, inspect


def only(snap, rule):
    """Findings of one rule (inspect(rules=[...]) keeps the run focused)."""
    return inspect(snap, rules=[rule]).findings


def statuses(snap, rule):
    return {f.status for f in only(snap, rule)}


# ----------------------------------------------------------------- baseline
def test_synthetic_baseline_is_clean(snap):
    rep = inspect(snap)
    assert rep.result() == "PASS", rep.text()
    assert not rep.actionable()
    assert rep.auto_actions() == []


# ---------------------------------------------------------------------- A.5
def test_a5_pass(snap):
    assert statuses(snap, "A.5") == {"pass"}


@pytest.mark.parametrize("value", ["", "TBD", "XXX", "-"])
def test_a5_placeholder_title_fails(snap, value):
    snap["title_block"]["title"] = value
    fails = [f for f in only(snap, "A.5") if f.status == "fail"]
    assert len(fails) == 1
    f = fails[0]
    assert "title" in f.message
    assert f.fix == [{"op": "set_title_block", "fields": {"title": "<title>"}}]
    assert not f.auto  # a title needs human input


def test_a5_missing_field_fails_without_fix(snap):
    del snap["title_block"]["sheet_number"]
    f = only(snap, "A.5")[0]
    assert f.status == "fail" and "sheet_number" in f.message and f.fix is None


def test_a5_scale_mismatch_auto_fix(snap):
    snap["title_block"]["scale"] = "1 : 2"
    rep = inspect(snap, rules=["A.5"])
    f = next(f for f in rep.findings if "does not match" in f.message)
    assert f.status == "fail" and f.auto
    assert f.fix == [{"op": "set_title_block", "fields": {"scale": "1 : 1"}}]
    assert {"op": "set_title_block", "fields": {"scale": "1 : 1"}} in rep.auto_actions()


def test_a5_scale_compare_ignores_spacing(snap):
    snap["title_block"]["scale"] = "1:1"
    assert statuses(snap, "A.5") == {"pass"}


def test_a5_reduced_scale_text(snap):
    for v in snap["views"]:
        if v.get("kind") == "ortho":
            v["scale"] = 0.5
    snap["title_block"]["scale"] = "1 : 1"
    f = next(f for f in only(snap, "A.5") if "does not match" in f.message)
    assert f.fix[0]["fields"]["scale"] == "1 : 2"


def test_a5_units_missing_auto_note(snap):
    snap["annotations"][0]["text"].remove("UNITS: mm")
    f = next(f for f in only(snap, "A.5") if "units" in f.message)
    assert f.status == "fail" and f.auto
    assert f.fix == [{"op": "set_note", "name": "Notes", "append": ["UNITS: mm"]}]


# ---------------------------------------------------------------------- A.7
def test_a7_pass(snap):
    assert statuses(snap, "A.7") == {"pass"}


def test_a7_symbol_mismatch_fails(snap):
    snap["sheet"]["projection_symbol"] = "third"
    f = only(snap, "A.7")[0]
    assert f.status == "fail" and "third-angle symbol" in f.message and "first-angle" in f.message


def test_a7_symbol_missing_fails(snap):
    snap["sheet"]["projection_symbol"] = None
    f = only(snap, "A.7")[0]
    assert f.status == "fail" and "missing" in f.message


# ---------------------------------------------------------------------- A.8
def test_a8_pass(snap):
    assert statuses(snap, "A.8") == {"pass"}


def test_a8_grouped_view_over_title_block_moves_group(snap):
    view(snap, "Front")["bbox"] = [240.0, 20.0, 320.0, 60.0]
    f = only(snap, "A.8")[0]
    assert f.status == "fail" and f.objects == ["Front"] and f.auto
    tb_top = snap["sheet"]["title_block"][3]
    assert f.fix == [{"op": "move_group", "name": "ProjGroup", "dx": 0, "dy": round(tb_top + 3 - 20.0, 2)}]


def test_a8_loose_view_over_title_block_moves_view(snap):
    v = view(snap, "Front")
    v["group"] = None
    v["bbox"] = [240.0, 20.0, 320.0, 60.0]
    f = only(snap, "A.8")[0]
    assert f.fix[0]["op"] == "move_view" and f.fix[0]["name"] == "Front"
    new_bottom = f.fix[0]["y"] - (60.0 - 20.0) / 2
    assert new_bottom > snap["sheet"]["title_block"][3]  # moved clear of the title block


def test_a8_view_outside_frame(snap):
    view(snap, "Front")["bbox"] = [5.0, 160.0, 85.0, 200.0]
    f = only(snap, "A.8")[0]
    assert f.status == "fail" and "outside the drawing frame" in f.message and not f.auto


def test_a8_note_over_title_block(snap):
    snap["annotations"][0]["bbox"] = [240.0, 20.0, 300.0, 40.0]
    f = only(snap, "A.8")[0]
    assert f.status == "fail" and f.auto and f.fix[0]["op"] == "set_note"
    assert f.fix[0]["y"] > snap["sheet"]["title_block"][3]


# ---------------------------------------------------------------------- B.3
def test_b3_pass(snap):
    assert statuses(snap, "B.3") == {"pass"}


def test_b3_circle_without_centre_mark(snap):
    view(snap, "Front")["circles"][0]["center_mark"] = False
    rep = inspect(snap, rules=["B.3"])
    f = rep.findings[0]
    assert f.status == "fail" and f.auto and f.objects == ["Front"]
    assert f.fix == [{"op": "add_centerlines", "view": "Front"}]
    assert f.bbox == [94.0, 174.0, 106.0, 186.0]
    assert rep.auto_actions() == [{"op": "add_centerlines", "view": "Front"}]


def test_b3_partial_arc_needs_no_centre_mark(snap):
    view(snap, "Front")["circles"][0].update(center_mark=False, full=False)
    assert statuses(snap, "B.3") == {"pass"}


def test_b3_no_circles_na(snap):
    view(snap, "Front")["circles"] = []
    assert statuses(snap, "B.3") == {"na"}


# ---------------------------------------------------------------------- C.2
def test_c2_pass_first_angle(snap):
    assert statuses(snap, "C.2") == {"pass"}


def test_c2_top_view_above_front_is_wrong_in_first_angle(snap):
    view(snap, "Top")["center"] = [100.0, 230.0]
    f = only(snap, "C.2")[0]
    assert f.status == "fail" and f.auto and f.objects == ["Top"]
    assert f.fix == [{"op": "move_view", "name": "Top", "x": 100.0, "y": 130.0}]


def test_c2_top_view_above_front_is_right_in_third_angle(snap):
    snap["projection"] = "third"
    snap["sheet"]["projection_symbol"] = "third"
    view(snap, "Top")["center"] = [100.0, 230.0]
    assert statuses(snap, "C.2") == {"pass"}


def test_c2_misaligned_view_fails(snap):
    view(snap, "Top")["center"] = [103.0, 130.0]  # below, but not aligned with the front view
    assert statuses(snap, "C.2") == {"fail"}


def test_c2_fix_for_view_beside_front_does_not_stack_on_front(snap):
    view(snap, "Top")["center"] = [180.0, 180.0]  # Top placed beside the front view, same height
    f = only(snap, "C.2")[0]
    front = view(snap, "Front")["center"]
    assert (f.fix[0]["x"], f.fix[0]["y"]) != tuple(front)


# ---------------------------------------------------------------------- C.6
def test_c6_pass(snap):
    assert statuses(snap, "C.6") == {"pass"}


def test_c6_hidden_lines_off(snap):
    view(snap, "Top")["hard_hidden"] = False
    f = only(snap, "C.6")[0]
    assert f.status == "fail" and f.auto and f.objects == ["Top"]
    assert f.fix == [{"op": "set_view", "name": "Top", "hard_hidden": True}]


# ---------------------------------------------------------------------- C.7
def test_c7_pass(snap):
    assert statuses(snap, "C.7") == {"pass"}


def test_c7_missing_isometric(snap):
    snap["views"] = [v for v in snap["views"] if v["kind"] != "pictorial"]
    f = only(snap, "C.7")[0]
    assert f.status == "fail" and "no isometric" in f.message


def test_c7_unshaded_isometric(snap):
    view(snap, "Iso")["shaded"] = False
    fs = only(snap, "C.7")
    assert [f.status for f in fs] == ["fail"]
    assert "not shaded" in fs[0].message


def test_c7_isometric_not_bottom_right_auto_move(snap):
    iso = view(snap, "Iso")
    iso["bbox"] = [40.0, 220.0, 100.0, 258.0]
    f = next(f for f in only(snap, "C.7") if "bottom-right" in f.message)
    assert f.status == "fail" and f.auto and f.fix[0]["op"] == "move_view"
    x, y = f.fix[0]["x"], f.fix[0]["y"]
    w, h = 60.0, 38.0
    new = [x - w / 2, y - h / 2, x + w / 2, y + h / 2]
    tb = snap["sheet"]["title_block"]
    assert new[1] >= tb[3] and new[2] <= snap["sheet"]["frame"][2] and not geom.overlap(new, tb)


def test_c7_reduced_scale_warns(snap):
    view(snap, "Iso")["scale"] = 0.2
    fs = only(snap, "C.7")
    assert {f.status for f in fs} == {"warn", "pass"}


# ---------------------------------------------------------------------- E.1
def test_e1_pass(snap):
    assert statuses(snap, "E.1") == {"pass"}


def test_e1_unplaced_requirement(snap):
    snap["meta"]["unplaced"] = ["slot width 8"]
    rep = inspect(snap, rules=["E.1"])
    f = rep.findings[0]
    assert f.status == "fail" and "slot width 8" in f.message
    assert rep.unplaced == ["slot width 8"]


def test_e1_missing_overall_size(snap):
    snap["dimensions"] = [d for d in snap["dimensions"] if d["name"] != "DimX"]
    fs = only(snap, "E.1")
    assert [f.status for f in fs] == ["fail"]
    assert "along X (80 mm)" in fs[0].message


def test_e1_thickness_note_counts_as_overall_size(snap):
    snap["dimensions"] = [d for d in snap["dimensions"] if d["name"] != "DimZ"]
    assert statuses(snap, "E.1") == {"fail"}
    snap["annotations"].append({"name": "Thickness", "kind": "note", "text": ["t10"], "bbox": [60.0, 150.0, 66.0, 155.0]})
    assert statuses(snap, "E.1") == {"pass"}


def test_e1_circle_without_size_auto_dimension(snap):
    snap["dimensions"] = [d for d in snap["dimensions"] if d["name"] != "DimD"]
    f = only(snap, "E.1")[0]
    assert f.status == "fail" and f.auto and "⌀12" in f.message
    act = f.fix[0]
    assert act["op"] == "add_dimension" and act["type"] == "Diameter" and act["view"] == "Front"
    assert act["refs"] == ["Edge5"] and act["format_spec"].startswith("⌀")


def test_e1_counterbore_diameter_in_callout_counts_as_sized(snap):
    view(snap, "Front")["circles"].append(circle("Edge6", (100.0, 180.0), 9.0))  # ⌀18 counterbore
    assert statuses(snap, "E.1") == {"fail"}
    d = dim(snap, "DimD")
    d["text"] = "⌀12 THRU\n⌴⌀18 ↧6"
    assert statuses(snap, "E.1") == {"pass"}


# ---------------------------------------------------------------------- E.4
def test_e4_pass(snap):
    assert statuses(snap, "E.4") == {"pass"}


def test_e4_missing_diameter_symbol(snap):
    d = dim(snap, "DimD")
    d.update(text="4× 12 THRU", format_spec="4× %.2w THRU")
    f = only(snap, "E.4")[0]
    assert f.status == "fail" and f.auto and f.objects == ["DimD"]
    assert f.fix == [{"op": "set_dimension", "name": "DimD", "format_spec": "4× ⌀%.2w THRU"}]


def test_e4_alternative_diameter_glyph_accepted(snap):
    dim(snap, "DimD")["text"] = "Ø12 THRU"
    assert statuses(snap, "E.4") == {"pass"}


def test_e4_radius_without_r(snap):
    snap["dimensions"].append(dict(dim(snap, "DimD"), name="DimR", type="Radius", value=3.0, text="3",
                                   format_spec="%.2w", signature="r3"))
    f = only(snap, "E.4")[0]
    assert f.status == "fail" and f.fix == [{"op": "set_dimension", "name": "DimR", "format_spec": "R%.2w"}]


# ---------------------------------------------------------------------- E.8
def _second_hole_callout(snap, view_name):
    d = dict(dim(snap, "DimD"), name="DimD2", view=view_name, signature="other-hole",
             text_box=[110.0, 168.0, 124.0, 172.0])
    snap["dimensions"].append(d)
    return d


def test_e8_pass(snap):
    assert statuses(snap, "E.8") == {"pass"}


def test_e8_duplicate_callouts_same_view(snap):
    view(snap, "Front")["circles"].append(circle("Edge6", (80.0, 180.0)))
    _second_hole_callout(snap, "Front")
    fs = [f for f in only(snap, "E.8") if "separate" in f.message]
    assert len(fs) == 1
    f = fs[0]
    assert f.status == "fail" and f.auto and f.objects == ["DimD", "DimD2"]
    assert f.fix == [{"op": "delete", "names": ["DimD2"]},
                     {"op": "set_dimension", "name": "DimD", "format_spec": "2× ⌀%.2w THRU"}]


def test_e8_same_callout_in_different_views_is_fine(snap):
    _second_hole_callout(snap, "Top")
    assert statuses(snap, "E.8") == {"pass"}


def test_e8_real_l_bracket_callouts_in_two_views_pass():
    s = load_fixture("l_bracket")
    texts = [(d["view"], d["text"]) for d in s["dimensions"] if d["type"] == "Diameter"]
    assert len({t for _, t in texts}) == 1 and len({v for v, _ in texts}) == 2  # same callout, two views
    assert statuses(s, "E.8") == {"pass"}


def test_e8_identical_holes_without_multiplicity(snap):
    view(snap, "Front")["circles"].append(circle("Edge6", (80.0, 180.0)))
    f = only(snap, "E.8")[0]
    assert f.status == "fail" and f.auto and "2 identical holes" in f.message
    assert f.fix == [{"op": "set_dimension", "name": "DimD", "format_spec": "2× ⌀%.2w THRU"}]
    dim(snap, "DimD")["text"] = "2× ⌀12 THRU"
    assert statuses(snap, "E.8") == {"pass"}


# ---------------------------------------------------------------------- E.9
def test_e9_pass(snap):
    assert statuses(snap, "E.9") == {"pass"}


@pytest.mark.parametrize("text,fmt", [("(80)", "(%.2w)"), ("[80]", "[%.2w]")])
def test_e9_brackets(snap, text, fmt):
    dim(snap, "DimX").update(text=text, format_spec=fmt)
    f = only(snap, "E.9")[0]
    assert f.status == "review" and not f.auto and f.objects == ["DimX"]
    assert not f.fix


# --------------------------------------------------------------------- E.10
def test_e10_pass(snap):
    assert statuses(snap, "E.10") == {"pass"}


def _apply_move(snap, act):
    d = dim(snap, act["name"])
    tb = d["text_box"]
    dx, dy = act["x"] - (tb[0] + tb[2]) / 2, act["y"] - (tb[1] + tb[3]) / 2
    d["text_box"] = geom.translate(tb, dx, dy)
    return d["text_box"]


def test_e10_text_overlap_auto_move_to_free_box(snap):
    dim(snap, "DimY")["text_box"] = [99.0, 206.0, 103.0, 210.0]  # on top of DimX's text
    rep = inspect(snap, rules=["E.10"])
    fails = [f for f in rep.findings if f.status == "fail"]
    assert len(fails) == 1
    f = fails[0]
    assert f.objects == ["DimY"] and "overlaps DimX" in f.message and f.auto
    act = f.fix[0]
    assert act["op"] == "move_dimension" and act["name"] == "DimY"
    assert rep.auto_actions() == [act]

    new_box = _apply_move(snap, act)
    c = checks.Ctx(snap)
    boxes, lines = checks.obstacles(c, "DimY")
    assert checks.is_free(c, new_box, boxes, lines, list(c.view_segments()))
    assert not geom.overlap(new_box, dim(snap, "DimX")["text_box"])
    assert statuses(snap, "E.10") == {"pass"}


def test_e10_text_over_part_geometry(snap):
    dim(snap, "DimD")["text_box"] = [126.0, 188.0, 142.0, 192.0]  # crosses the right edge x=140
    f = next(f for f in only(snap, "E.10") if f.status == "fail")
    assert "part geometry" in f.message and f.auto
    _apply_move(snap, f.fix[0])
    assert statuses(snap, "E.10") == {"pass"}


def test_e10_real_l_bracket_fix_resolves_overlap():
    s = load_fixture("l_bracket")
    f = next(f for f in only(s, "E.10") if f.status == "fail")
    assert f.auto and f.fix[0]["op"] == "move_dimension"
    _apply_move(s, f.fix[0])
    assert "fail" not in statuses(s, "E.10")


def test_e10_crossing_dimension_lines_warn(snap):
    dim(snap, "DimD")["lines"] = [[100.0, 195.0, 100.0, 215.0]]  # crosses DimX's dimension line at y=204
    assert "warn" in statuses(snap, "E.10")


# --------------------------------------------------------------------- E.11
def test_e11_pass(snap):
    assert statuses(snap, "E.11") == {"pass"}


def test_e11_duplicate_signature(snap):
    snap["dimensions"].append(dict(dim(snap, "DimX"), name="DimX2", text_box=[97.0, 215.0, 103.0, 219.0]))
    f = only(snap, "E.11")[0]
    assert f.status == "fail" and f.auto and f.objects == ["DimX", "DimX2"]
    assert f.fix == [{"op": "delete", "names": ["DimX2"]}]


# ---------------------------------------------------------------------- K.1
def test_k1_pass(snap):
    assert statuses(snap, "K.1") == {"pass"}


@pytest.mark.parametrize("value", ["", "Stainless steel Mat.No. 1.4301", None])
def test_k1_material_placeholder(snap, value):
    if value is None:
        del snap["title_block"]["part_material"]
    else:
        snap["title_block"]["part_material"] = value
    f = only(snap, "K.1")[0]
    assert f.status == "fail" and not f.auto
    assert f.fix[0]["op"] == "set_title_block" and "part_material" in f.fix[0]["fields"]


# ---------------------------------------------------------------------- N.3
def test_n3_pass(snap):
    assert statuses(snap, "N.3") == {"pass"}


def test_n3_missing_edge_break(snap):
    snap["annotations"][0]["text"].remove("BREAK ALL SHARP EDGES 0.2-0.5")
    f = only(snap, "N.3")[0]
    assert f.status == "review" and not f.auto
    assert not f.fix


def test_n3_missing_edge_break_without_notes_adds_note(snap):
    snap["annotations"] = []
    f = only(snap, "N.3")[0]
    assert f.status == "review" and not f.fix


def test_n3_vague_deburr_warns(snap):
    notes = snap["annotations"][0]["text"]
    notes[notes.index("BREAK ALL SHARP EDGES 0.2-0.5")] = "DEBURR"
    assert statuses(snap, "N.3") == {"warn"}


# ---------------------------------------------------------------------- P.1
def test_p1_pass(snap):
    assert statuses(snap, "P.1") == {"pass"}


@pytest.mark.parametrize("change", [{"show_units": True}, {"text": "80 mm"}, {"text": "80 [3.15]"}])
def test_p1_units_in_text(snap, change):
    dim(snap, "DimX").update(change)
    f = only(snap, "P.1")[0]
    assert f.status == "fail" and f.auto and f.objects == ["DimX"]
    assert f.fix == [{"op": "set_dimension", "name": "DimX", "show_units": False}]


# ---------------------------------------------------------------------- P.5
def test_p5_pass(snap):
    assert statuses(snap, "P.5") == {"pass"}


def test_p5_dedupe_not_run(snap):
    snap["meta"]["dedupe_ran"] = False
    f = only(snap, "P.5")[0]
    assert f.status == "fail" and f.auto and f.fix == [{"op": "dedupe_dimensions"}]


def test_p5_duplicates_remain_after_dedupe(snap):
    snap["dimensions"].append(dict(dim(snap, "DimX"), name="DimX2"))
    assert statuses(snap, "P.5") == {"fail"}


# ------------------------------------------------------------ robustness
def test_crashing_section_is_reported_not_raised(snap):
    del dim(snap, "DimX")["tolerance"]  # rules_F reads d["tolerance"]
    rep = inspect(snap)
    crash = by_rule(rep, "F.*")
    assert crash and crash[0].status == "warn" and "crashed" in crash[0].message
    assert by_rule(rep, "E.1")  # other sections still ran


def test_inspect_does_not_mutate_snapshot(snap):
    before = make_snapshot()
    inspect(snap)
    assert snap == before
