"""Inspect the strokes actually exported, including outside text and leaders."""
import importlib.util
import math
from conftest import ROOT

spec = importlib.util.spec_from_file_location("annotation_ink", ROOT / "addon/FreeDrawingMCP/fd_drawing/annotation_ink.py")
ink = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ink)


def test_angular_arc_and_witnesses_follow_label_radius_without_moving_references():
    record = {"type": "Angle", "angle_geometry": {"vertex": [0, 0], "ends": [[10, 0], [0, 10]]},
              "text_center": [20, 20], "text_box": [16, 18, 24, 22], "lines": []}
    points, extensions = ink.angular_ink(record)
    assert len(points) > 8 and len(extensions) == 2
    assert abs(points[0][1]) < 1e-10 and abs(points[-1][0]) < 1e-10
    assert points[0][0] > 20 and points[-1][1] > 20
    assert extensions[0][:2] == [11, 0]
    shifted = ink.shifted_dimension(record, 10, 10)
    new_points, _ = ink.angular_ink(shifted)
    assert math.dist(new_points[0], [0, 0]) > math.dist(points[0], [0, 0])
    assert shifted['angle_geometry'] == record['angle_geometry']
    assert shifted['ink_lines'] != ink.dimension_ink(record)
    opposite = ink.shifted_dimension(record, -40, -40)
    opposite_points, _ = ink.angular_ink(opposite)
    assert opposite_points[0][0] < 0 and opposite_points[-1][1] < 0
    assert math.dist(opposite_points[0], [0, 0]) == math.dist(points[0], [0, 0])


def test_short_dimension_accounts_for_outside_arrows_and_text():
    record = {"type": "DistanceX", "text_center": [14, 12], "text_box": [10, 10, 18, 14],
              "lines": [[0, 9, 3, 9], [0, 0, 0, 9], [3, 0, 3, 9]]}
    strokes = ink.dimension_ink(record)
    assert strokes == [[-5, 9, 18.5, 9], [0, 1, 0, 11], [3, 1, 3, 11]]


def test_radial_leader_includes_shoulder_below_text():
    record = {"type": "Diameter", "arc": {"center": [0, 0], "radius": 4},
              "text_center": [15, 10], "text_box": [10, 8, 20, 12]}
    strokes = ink.dimension_ink(record)
    assert strokes[0][2:] == [9.2, 7.6]
    assert strokes[1] == [9.2, 7.6, 20.5, 7.6]


def test_radius_leader_starts_on_referenced_arc_not_missing_part_of_full_circle():
    record = {"type": "Radius", "arc": {"center": [0, 0], "radius": 10,
              "points": [[10, 0], [8, 6], [6, 8], [0, 10]]},
              "text_center": [-20, -20], "text_box": [-23, -22, -17, -18]}
    strokes = ink.dimension_ink(record)
    assert strokes[0][:2] in record["arc"]["points"]
    assert all(value >= 0 for value in strokes[0][:2])


def test_shift_rebuilds_outside_line_without_moving_feature_references():
    record = {"type": "DistanceX", "text_center": [5, 12], "text_box": [3, 10, 7, 14],
              "lines": [[0, 9, 10, 9], [0, 0, 0, 9], [10, 0, 10, 9]]}
    shifted = ink.shifted_dimension(record, 20, 8)
    assert shifted["lines"][1][:2] == [0, 0]
    assert shifted["ink_lines"][0] == [0, 17, 27.5, 17]
    assert record["text_center"] == [5, 12]


def test_crossing_and_collinear_overlap_are_collisions_but_shared_endpoint_is_not():
    assert ink.strokes_conflict([0, 0, 10, 0], [5, -5, 5, 5])
    assert ink.strokes_conflict([0, 0, 10, 0], [5, 0, 15, 0])
    assert not ink.strokes_conflict([0, 0, 10, 0], [10, 0, 20, 0])
    assert ink.strokes_conflict([0, 0, 10, 0], [1, .1, 8, .15])


def test_caption_is_below_dimension_stack_not_only_view_outline():
    view = {"name": "DetailA", "caption": "A (2:1)", "bbox": [20, 40, 60, 80]}
    dims = [{"view": "DetailA", "text_box": [30, 10, 40, 15], "ink_lines": [[20, 8, 50, 8]]}]
    position, box = ink.caption_geometry(view, dims)
    assert position == [40, 0]
    assert box[3] < 8
