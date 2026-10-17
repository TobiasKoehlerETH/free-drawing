"""Locate ISO 5457 TechDraw templates and read their sheet geometry.

The title-block keep-out zone, drawing frame and projection symbol are read
from the template SVG itself, so the planner and the rules engine work for
any template that follows FreeCAD's ISO naming (ids such as
``title_block_frame``, ``drawing_space_frame``, ``first_angle_*``).

All returned boxes are page coordinates: millimetres, origin bottom-left,
Y up (TechDraw's page frame). Boxes are ``[x0, y0, x1, y1]``.
"""

import os
import re
import xml.etree.ElementTree as ET

from .standards import SHEET_SIZES

_FREECAD_NS = "https://www.freecad.org/wiki/index.php?title=Svg_Namespace"
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def template_dirs():
    dirs = []
    try:
        import FreeCAD

        res = FreeCAD.getResourceDir()
        dirs.append(os.path.join(res, "Mod", "TechDraw", "Templates", "ISO"))
        dirs.append(os.path.join(res, "Mod", "TechDraw", "Templates"))
    except Exception:
        pass
    return [d for d in dirs if os.path.isdir(d)]


def resolve_template(spec="A3", orientation="landscape", style="advanced"):
    """Return a template path for ``spec``.

    ``spec`` is a path to an SVG, or a sheet name such as ``A3`` / ``A4``.
    """
    if spec and os.path.isfile(spec):
        return os.path.abspath(spec)
    size = (spec or "A3").upper()
    if size not in SHEET_SIZES:
        raise ValueError(f"unknown sheet size {spec!r}; use one of {sorted(SHEET_SIZES)} or a template path")
    orient = "Portrait" if str(orientation).lower().startswith("p") else "Landscape"
    names = [
        f"{size}_{orient}_ISO5457_{style}.svg",
        f"{size}_{orient}_ISO5457_advanced.svg",
        f"{size}_{orient}_ISO5457_minimal.svg",
    ]
    for d in template_dirs():
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    raise FileNotFoundError(f"no ISO template for {size} {orient} in {template_dirs()}")


def _floats(text):
    return [float(v) for v in _NUM.findall(text or "")]


def _path_points(d):
    """Rough absolute point list for a path ``d`` (enough for a bounding box)."""
    pts = []
    x = y = 0.0
    tokens = re.findall(r"[MmLlHhVvZzCcSsQqTtAa]|" + _NUM.pattern, d or "")
    cmd = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                continue
            continue
        if cmd in ("M", "L", "T"):
            x, y = float(tokens[i]), float(tokens[i + 1]); i += 2
        elif cmd in ("m", "l", "t"):
            x += float(tokens[i]); y += float(tokens[i + 1]); i += 2
        elif cmd == "H":
            x = float(tokens[i]); i += 1
        elif cmd == "h":
            x += float(tokens[i]); i += 1
        elif cmd == "V":
            y = float(tokens[i]); i += 1
        elif cmd == "v":
            y += float(tokens[i]); i += 1
        elif cmd in ("C", "S", "Q"):
            n = {"C": 6, "S": 4, "Q": 4}[cmd]
            vals = [float(v) for v in tokens[i:i + n]]; i += n
            x, y = vals[-2], vals[-1]
        elif cmd in ("c", "s", "q"):
            n = {"c": 6, "s": 4, "q": 4}[cmd]
            vals = [float(v) for v in tokens[i:i + n]]; i += n
            x += vals[-2]; y += vals[-1]
        elif cmd in ("A", "a"):
            vals = [float(v) for v in tokens[i:i + 7]]; i += 7
            if cmd == "A":
                x, y = vals[5], vals[6]
            else:
                x += vals[5]; y += vals[6]
        else:
            i += 1
            continue
        pts.append((x, y))
    return pts


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _elem_box(el):
    tag = el.tag.split("}")[-1]
    try:
        if tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            return [x, y, x + float(el.get("width", 0)), y + float(el.get("height", 0))]
        if tag == "circle":
            cx, cy, r = float(el.get("cx", 0)), float(el.get("cy", 0)), float(el.get("r", 0))
            return [cx - r, cy - r, cx + r, cy + r]
        if tag == "line":
            xs = [float(el.get("x1", 0)), float(el.get("x2", 0))]
            ys = [float(el.get("y1", 0)), float(el.get("y2", 0))]
            return [min(xs), min(ys), max(xs), max(ys)]
        if tag == "path":
            pts = _path_points(el.get("d"))
            if pts:
                return [min(p[0] for p in pts), min(p[1] for p in pts),
                        max(p[0] for p in pts), max(p[1] for p in pts)]
        if tag == "text" and el.get("x") is not None:
            x, y = float(el.get("x")), float(el.get("y", 0))
            return [x, y - 3.5, x, y]
    except (TypeError, ValueError):
        return None
    return None


def parse_template(path, width=None, height=None):
    """Read sheet geometry from a template SVG.

    Returns ``{"width", "height", "frame", "title_block", "projection_symbol",
    "fields"}`` where ``fields`` maps editable names to their (x, y) anchor.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    vb = _floats(root.get("viewBox"))
    w = width or (vb[2] if len(vb) == 4 else _floats(root.get("width"))[0])
    h = height or (vb[3] if len(vb) == 4 else _floats(root.get("height"))[0])

    frame = None
    centring = False
    tb_boxes = []
    projection = None
    fields = {}
    for el in root.iter():
        eid = (el.get("id") or "").lower()
        box = _elem_box(el)
        if eid == "drawing_space_frame" and box:
            frame = box
        if "centring" in eid or "centering" in eid:
            centring = True
        if box and ("title_block" in eid or eid.startswith("first_angle") or eid.startswith("third_angle")):
            tb_boxes.append(box)
        if eid.startswith("first_angle"):
            projection = "first"
        elif eid.startswith("third_angle"):
            projection = "third"
        editable = el.get(f"{{{_FREECAD_NS}}}editable")
        if editable:
            try:
                fx, fy = float(el.get("x", 0)), float(el.get("y", 0))
            except ValueError:
                continue
            fields[editable] = (fx, fy)
            tb_boxes.append([fx - 1, fy - 5, fx + 1, fy + 1])
    tb = _union(tb_boxes)
    if frame is None:
        frame = [10.0, 10.0, w - 10.0, h - 10.0]

    def flip(b):
        return None if b is None else [round(b[0], 3), round(h - b[3], 3), round(b[2], 3), round(h - b[1], 3)]

    # Clamp the title block to the frame: label text can poke past borders.
    tbf = flip(tb)
    fr = flip(frame)
    if tbf:
        tbf = [max(tbf[0], fr[0]), max(tbf[1], fr[1]), min(tbf[2], fr[2]), min(tbf[3], fr[3])]
    return {
        "width": float(w),
        "height": float(h),
        "frame": fr,
        "title_block": tbf,
        "projection_symbol": projection,
        "centring_marks": centring,
        "fields": {k: [round(v[0], 2), round(h - v[1], 2)] for k, v in fields.items()},
        "template": os.path.basename(path),
    }


def sheet_name(width, height):
    w, h = max(width, height), min(width, height)
    for name, (sw, sh) in SHEET_SIZES.items():
        if abs(sw - w) < 1 and abs(sh - h) < 1:
            return name
    return None
