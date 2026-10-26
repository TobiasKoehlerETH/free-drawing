"""End-to-end through headless FreeCAD: create -> snapshot -> inspect -> fix -> render -> export.

Runs three freecadcmd processes (about 1-2 minutes in total). Skipped when
FreeCAD is not installed. Select / deselect with ``-m freecad`` / ``-m "not freecad"``.
"""

from __future__ import annotations

import base64
import struct
import zlib

import pytest
from conftest import ROOT

from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd
from free_drawing_mcp.rules import highlights, inspect

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.skipif(detect_freecadcmd() is None, reason="freecadcmd not found"),
]

SOURCE = ROOT / "samples" / "parts" / "boss_plate.step"
MATERIAL = "EN AW-6082 T6"


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    length, ctype = struct.unpack(">I4s", data[8:16])
    assert ctype == b"IHDR" and length == 13
    w, h = struct.unpack(">II", data[16:24])
    # walk the chunks: CRCs must match and the image data must inflate
    pos, idat = 8, b""
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        assert zlib.crc32(typ + body) & 0xFFFFFFFF == crc, f"bad CRC in {typ!r}"
        if typ == b"IDAT":
            idat += body
        pos += 12 + n
        if typ == b"IEND":
            break
    assert zlib.decompress(idat)
    return w, h


@pytest.fixture(scope="module")
def drawing(tmp_path_factory):
    out = tmp_path_factory.mktemp("fd") / "boss_plate_drawing.FCStd"
    be = HeadlessBackend(timeout=300)
    created, snap = be.run([
        {"op": "create_drawing", "args": {"source": str(SOURCE), "output": str(out)}},
        {"op": "snapshot", "args": {"doc": str(out), "include_geometry": True}},
    ])
    return be, out, created, snap


def test_create_and_inspect(drawing):
    be, out, created, snap = drawing
    assert out.is_file() and created["file"]
    rep = inspect(snap)
    bad = [f for f in rep.findings if f.rule in ("E.1", "E.10", "E.11") and f.status == "fail"]
    assert not bad, rep.text()
    ortho = [v for v in snap["views"] if v["kind"] == "ortho"]
    pictorial = [v for v in snap["views"] if v["kind"] == "pictorial"]
    assert len(ortho) == 2, [v["projection_type"] for v in ortho]
    assert len(pictorial) == 1 and pictorial[0]["shaded"]
    iso_box = pictorial[0]["bbox"]
    assert iso_box[0] > snap["sheet"]["width"] * 0.55
    assert iso_box[1] >= snap["sheet"]["title_block"][3]
    from free_drawing_mcp.rules.geom import overlap
    assert not any(overlap(iso_box, d["text_box"]) for d in snap["dimensions"] if d.get("text_box"))
    assert not any(overlap(iso_box, v["bbox"]) for v in ortho)
    assert all(any(d["view"] == v["name"] for d in snap["dimensions"]) for v in ortho)
    assert snap["title_block"]["scale"] == created["scale"]
    assert len({v["scale"] for v in ortho}) == 1
    assert [f.status for f in rep.findings if f.rule == "K.1"] == ["fail"]


def test_apply_action_render_export(drawing, tmp_path):
    be, out, _, _ = drawing
    applied, snap = be.run([
        {"op": "apply_actions", "args": {"doc": str(out), "actions": [
            {"op": "set_title_block", "fields": {"part_material": MATERIAL}}]}},
        {"op": "snapshot", "args": {"doc": str(out)}},
    ])
    assert all(r["ok"] for r in applied["results"]), applied
    assert snap["title_block"]["part_material"] == MATERIAL
    rep = inspect(snap)
    assert [f.status for f in rep.findings if f.rule == "K.1"] == ["pass"]

    pdf = tmp_path / "boss_plate.pdf"
    img, exported = be.run([
        {"op": "render", "args": {"doc": str(out), "width": 1600, "highlights": highlights(rep)}},
        {"op": "export", "args": {"doc": str(out), "format": "pdf", "path": str(pdf)}},
    ])
    png = base64.b64decode(img["png_base64"])
    assert len(png) > 10_000
    w, h = _png_size(png)
    assert w == 1600 and 1000 < h < 1300  # A3 landscape aspect
    assert exported["path"].lower() == str(pdf).lower() or pdf.is_file()
    assert pdf.is_file() and pdf.stat().st_size > 1000
    assert pdf.read_bytes()[:5] == b"%PDF-"
