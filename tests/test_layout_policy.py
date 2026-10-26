"""Plan transformations preserve feature coverage and readable stack demand."""
import importlib.util
from types import SimpleNamespace

from conftest import ROOT

spec = importlib.util.spec_from_file_location("layout_policy", ROOT / "addon/FreeDrawingMCP/fd_drawing/layout_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def dimensions(count, view, start=0):
    return [{"requirement_id": i, "view": view, "side": "top", "span": (0, i + 1),
             "refs": ["Vertex1", f"Vertex{i + 2}"]} for i in range(start, start + count)]


def test_balance_preserves_every_dimension_and_datum_without_mutating_original():
    view = SimpleNamespace(name="Front")
    original = dimensions(11, view)
    result = policy.balanced_stacks(original)
    assert sum(s["side"] == "top" for s in result) == 5
    assert sum(s["side"] == "bottom" for s in result) == 6
    assert all(s["side"] == "top" for s in original)
    assert [(s["requirement_id"], s["refs"], s["span"]) for s in result] == [
        (s["requirement_id"], s["refs"], s["span"]) for s in original]
    assert policy.balanced_stacks(result) == result


def test_equal_dimension_counts_cannot_hide_a_lost_requirement():
    original = [{"requirement_id": 0}, {"requirement_id": 1}]
    assert not policy.preserves_coverage(original, [{"requirement_id": 0}, {"requirement_id": 2}])
    assert policy.preserves_coverage(original, [{"requirement_id": 1}, {"requirement_id": 0}, {"requirement_id": 2}])


def test_parent_keeps_a_useful_dimension_without_duplicating_it():
    parent = SimpleNamespace(Name="Front")
    parent_geom = SimpleNamespace(name="Front", view=parent)
    detail_geom = SimpleNamespace(name="DetailA", view=SimpleNamespace(Name="DetailA", BaseView=parent))
    original = dimensions(3, parent_geom)
    revised = dimensions(3, detail_geom)
    result = policy.retain_parent_dimensions(original, revised)
    assert [s["view"].name for s in result] == ["DetailA", "DetailA", "Front"]
    assert {s["requirement_id"] for s in result} == {0, 1, 2}
    assert all(s["view"].name == "DetailA" for s in revised)


def test_parent_never_steals_the_only_dimension_or_reintroduces_hidden_geometry():
    parent = SimpleNamespace(Name="Front")
    parent_geom = SimpleNamespace(name="Front", view=parent)
    detail_geom = SimpleNamespace(name="DetailA", view=SimpleNamespace(Name="DetailA", BaseView=parent))
    original, revised = dimensions(1, parent_geom), dimensions(1, detail_geom)
    assert policy.retain_parent_dimensions(original, revised) == revised
    original, revised = dimensions(2, parent_geom), dimensions(2, detail_geom)
    for item in original:
        item["hidden"] = True
    assert policy.retain_parent_dimensions(original, revised) == revised


def test_pack_keeps_complete_blocks_inside_frame_and_outside_title_block():
    blocks = [("standard", 60, 65), ("section", 30, 30), ("detail", 25, 25)]
    title = [50, 0, 100, 25]
    positions = policy.pack_blocks(blocks, [0, 0, 100, 100], [title])
    assert positions is not None
    occupied = [title]
    for name, width, height in blocks:
        x, y = positions[name]
        box = [x, y, x + width, y + height]
        assert 0 <= x <= 100 - width and 0 <= y <= 100 - height
        assert not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in occupied)
        occupied.append(box)


def test_pack_reports_no_fit_without_shrinking_or_clipping():
    assert policy.pack_blocks([("view", 101, 80)], [0, 0, 100, 100], []) is None


def test_projection_group_can_fit_around_title_block_without_changing_alignment():
    blocks = [("standard", 100, 100)]
    title = [60, 0, 100, 40]
    assert policy.pack_blocks(blocks, [0, 0, 100, 100], [title]) is None
    footprints = {"standard": [[0, 0, 50, 100], [50, 50, 100, 100]]}
    assert policy.pack_blocks(blocks, [0, 0, 100, 100], [title], footprints=footprints) == {"standard": (0, 0)}
