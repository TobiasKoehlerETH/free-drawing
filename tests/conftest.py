"""Shared fixtures: real snapshots (tests/fixtures/*.snapshot.json) and a
synthetic, fully compliant snapshot factory for targeted rule tests.

Synthetic layout (page mm, origin bottom-left, Y up, A3 landscape):

    Front view  80 x 40 plate face, one ⌀12 hole at its centre
    Top view    80 x 10 strip below the front view (first-angle)
    Iso view    shaded 1 : 2 pictorial bottom-right above the title block
    Dimensions  80 (X), 40 (Y), 10 (Z, in Top), ⌀12 THRU
    Notes       units, ISO 2768-m, edge break, Ra default

``make_snapshot()`` returns a fresh deep copy so tests can mutate freely.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_FIXTURES = ["plate_hole_pattern", "flange", "l_bracket"]


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.snapshot.json").read_text(encoding="utf-8"))


def _dim(name, typ, view, value, text, text_box, lines, *, refs=None, fmt="%.2w", axis=None, role="", sig=None):
    return {
        "name": name, "label": name, "type": typ, "view": view,
        "refs": refs if refs is not None else ["Vertex1", "Vertex2"],
        "value": value, "text": text, "format_spec": fmt, "arbitrary": False, "show_units": False,
        "theoretical_exact": False, "tolerance": {"over": 0.0, "under": 0.0, "equal": True},
        "role": role, "lines": lines, "text_box": text_box,
        "text_center": [(text_box[0] + text_box[2]) / 2, (text_box[1] + text_box[3]) / 2],
        "ref_hidden": False, "model_axis": axis,
        "signature": sig or json.dumps([typ, view, value, name]),
    }


def _rect(x0, y0, x1, y1):
    return [[x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]]


def _ortho(name, ptype, center, bbox, axis, circles=()):
    return {
        "name": name, "label": ptype, "type_id": "TechDraw::DrawProjGroupItem", "kind": "ortho",
        "center": list(center), "caption": "", "scale": 1.0, "projection_type": ptype, "group": "ProjGroup",
        "axis": axis, "hard_hidden": True, "smooth_visible": False, "edges_visible": 5, "edges_hidden": 0,
        "bbox": list(bbox), "line_widths": {"visible": 0.7, "hidden": 0.35, "center": 0.35, "source": "view"},
        "shaded": False, "circles": list(circles), "cosmetic": [], "polylines_visible": _rect(*bbox),
        "polylines_hidden": [],
    }


def circle(edge="Edge5", center=(100.0, 180.0), radius=6.0, center_mark=True, full=True):
    return {"edge": edge, "center": list(center), "radius": radius, "model_radius": radius, "full": full,
            "visible": True, "center_mark": center_mark, "edges": [edge]}


_BASE = {
    "schema": 1,
    "document": "synthetic",
    "file": "synthetic.FCStd",
    "page": "Page",
    "units": "mm",
    "sheet": {
        "width": 420.0, "height": 297.0, "frame": [20.0, 10.0, 410.0, 287.0],
        "title_block": [230.0, 10.0, 410.0, 76.0], "projection_symbol": "first", "centring_marks": True,
        "template": "A3_Landscape_ISO5457_advanced.svg", "size_name": "A3", "orientation": "landscape",
    },
    "title_block": {
        "title": "Synthetic plate", "scale": "1 : 1", "revision_index": "A", "sheet_number": "1 / 1",
        "general_tolerances": "ISO 2768-m", "part_material": "EN AW-6082 T6", "drawing_number": "SP-001",
    },
    "projection": "first",
    "views": [
        {"name": "ProjGroup", "label": "ProjGroup", "type_id": "TechDraw::DrawProjGroup", "kind": "group",
         "x": 100.0, "y": 180.0, "scale": 1.0},
        _ortho("Front", "Front", (100.0, 180.0), (60.0, 160.0, 140.0, 200.0), "Z", [circle()]),
        _ortho("Top", "Top", (100.0, 130.0), (60.0, 125.0, 140.0, 135.0), "Y"),
        {"name": "Iso", "label": "Iso", "type_id": "TechDraw::DrawViewPart", "kind": "pictorial",
         "center": [370.0, 101.0], "scale": 0.5, "shaded": True, "bbox": [340.0, 82.0, 400.0, 120.0],
         "polylines_visible": [], "polylines_hidden": []},
    ],
    "dimensions": [
        _dim("DimX", "DistanceX", "Front", 80.0, "80", [97.0, 205.0, 103.0, 209.0],
             [[60.0, 204.0, 140.0, 204.0], [60.0, 200.0, 60.0, 206.0], [140.0, 200.0, 140.0, 206.0]],
             axis="X", role="baseline:X:face"),
        _dim("DimY", "DistanceY", "Front", 40.0, "40", [146.0, 178.0, 150.0, 182.0],
             [[144.0, 160.0, 144.0, 200.0], [140.0, 160.0, 146.0, 160.0], [140.0, 200.0, 146.0, 200.0]],
             axis="Y", role="baseline:Y:face"),
        _dim("DimZ", "DistanceY", "Top", 10.0, "10", [146.0, 128.0, 150.0, 132.0],
             [[144.0, 125.0, 144.0, 135.0], [140.0, 125.0, 146.0, 125.0], [140.0, 135.0, 146.0, 135.0]],
             axis="Z", role="baseline:Z:face"),
        _dim("DimD", "Diameter", "Front", 12.0, "⌀12 THRU", [110.0, 188.0, 124.0, 192.0],
             [[105.0, 180.0, 110.0, 188.0]], refs=["Edge5"], fmt="⌀%.2w THRU", role="diameter:hole"),
    ],
    "annotations": [
        {"name": "Notes", "label": "Notes", "type_id": "TechDraw::DrawViewAnnotation", "kind": "note",
         "text": ["UNITS: mm", "GENERAL TOLERANCES: ISO 2768-m", "BREAK ALL SHARP EDGES 0.2-0.5",
                  "U.O.S. SURFACE: Ra 3.2 µm"],
         "size": 3.5, "bbox": [26.0, 16.0, 91.0, 42.0]},
    ],
    "meta": {"generator": "free-drawing-mcp", "dedupe_ran": True, "part_kind": "plate", "assembly": False,
             "unplaced": []},
    "model": {"solids": 1, "extents": {"X": 80.0, "Y": 40.0, "Z": 10.0}},
    "gui": False,
}


def make_snapshot() -> dict:
    return copy.deepcopy(_BASE)


def view(snap: dict, name: str) -> dict:
    return next(v for v in snap["views"] if v["name"] == name)


def dim(snap: dict, name: str) -> dict:
    return next(d for d in snap["dimensions"] if d["name"] == name)


def by_rule(report, rule: str, status: str | None = None):
    return [f for f in report.findings if f.rule == rule and (status is None or f.status == status)]


@pytest.fixture
def snap() -> dict:
    return make_snapshot()


@pytest.fixture(params=REAL_FIXTURES)
def real_snapshot(request) -> dict:
    return load_fixture(request.param)
