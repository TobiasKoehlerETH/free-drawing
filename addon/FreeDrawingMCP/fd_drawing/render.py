"""Render a page (plus inspection highlights) to SVG / PNG / PDF.

Two sources:

* ``schematic`` - built from the snapshot: the real template, every projected
  edge (visible thick, hidden dashed thin), centre lines, dimensions and
  notes. Works in console mode (freecadcmd) and shows exactly what the rules
  engine measured.
* ``techdraw`` - TechDraw's own SVG export (GUI only), for final visual QA.

Highlights are ``{"bbox": [x0,y0,x1,y1], "label": "E.10", "severity":
"fail|warn|review"}`` in page coordinates and are drawn on top of either.
"""

import base64
import html
import os
import re
import tempfile
import xml.etree.ElementTree as ET

import FreeCAD as App

from .standards import DIM_FONT_SIZE, LINE_THIN
from .annotation_ink import angular_ink, dimension_ink

_SVG_NS = "http://www.w3.org/2000/svg"
_FC_NS = "https://www.freecad.org/wiki/index.php?title=Svg_Namespace"
ET.register_namespace("", _SVG_NS)
ET.register_namespace("freecad", _FC_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

COLORS = {"fail": "#e0201b", "warn": "#f08c00", "review": "#1c7ed6", "info": "#2b8a3e"}


def _esc(s):
    return html.escape(str(s), quote=True)


def _template_group(template_path, fields):
    """Template SVG children with editable fields filled, as an SVG string."""
    try:
        tree = ET.parse(template_path)
    except Exception:
        return ""
    root = tree.getroot()
    for el in root.iter():
        name = el.get(f"{{{_FC_NS}}}editable")
        if name is None:
            continue
        value = fields.get(name, "")
        for child in list(el):
            el.remove(child)
        el.text = value
    parts = []
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag in ("metadata", "title"):
            continue
        parts.append(ET.tostring(child, encoding="unicode"))
    return "".join(parts)


def _poly(points_flat, H):
    pts = [f"{points_flat[i]:.2f},{H - points_flat[i + 1]:.2f}" for i in range(0, len(points_flat) - 1, 2)]
    return " ".join(pts)


def _cosmetic_svg(c, H):
    """Preserve the stored TechDraw line style for straight and curved guides."""
    fmt = c.get("format") or {}
    if not fmt.get("visible", True):
        return ""
    style = int(fmt.get("style", 4))
    dash = {2: "3,1.5", 3: "0.5,1.5", 4: "6,1,0.5,1", 5: "6,1,0.5,1,0.5,1"}.get(style)
    attrs = f'stroke-width="{float(fmt.get("weight", 0.25))}"'
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    color = fmt.get("color")
    if color and len(color) >= 3:
        attrs += ' stroke="#{:02x}{:02x}{:02x}"'.format(*(max(0, min(255, round(float(x) * 255))) for x in color[:3]))
    if c.get("geometry") == "circle":
        center = c["center"]
        return f'<circle cx="{center[0]}" cy="{H - center[1]}" r="{c["radius"]}" {attrs}/>'
    return f'<polyline points="{_poly(c.get("points", []), H)}" {attrs}/>'


def schematic_svg(snap, highlights=None, template_path=None):
    W = snap["sheet"]["width"]
    H = snap["sheet"]["height"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>']
    if template_path and os.path.isfile(template_path):
        out.append('<g id="template">' + _template_group(template_path, snap.get("title_block", {})) + "</g>")
    out.append('<g id="views" fill="none" stroke="#000" stroke-linecap="round" stroke-linejoin="round">')
    for v in snap.get("views", []):
        img = v.get("image_file")
        if img and v.get("bbox") and os.path.isfile(img):
            with open(img, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            bb = v["bbox"]
            out.append(f'<image x="{bb[0]}" y="{H - bb[3]}" width="{bb[2] - bb[0]}" height="{bb[3] - bb[1]}" '
                       f'preserveAspectRatio="none" xlink:href="data:image/png;base64,{data}"/>')
        lw = (v.get("line_widths") or {})
        for pl in v.get("polylines_hidden", []):
            out.append(f'<polyline points="{_poly(pl, H)}" stroke-width="{lw.get("hidden", 0.35)}" stroke-dasharray="3,1.5"/>')
        for pl in v.get("polylines_visible", []):
            out.append(f'<polyline points="{_poly(pl, H)}" stroke-width="{lw.get("visible", 0.7)}"/>')
        for c in v.get("cosmetic", []):
            out.append(_cosmetic_svg(c, H))
        if v.get("kind") == "section" and v.get("hatch"):
            out.append(_hatch_svg(v["hatch"], H, f"hatch{len(out)}"))
        if v.get("kind") == "section" and v.get("cutting_line"):
            out.append(_cutting_line_svg(v["cutting_line"], H))
        if v.get("kind") == "detail" and v.get("detail_circle"):
            out.append(_detail_circle_svg(v["detail_circle"], v.get("reference", ""), H))
        if v.get("kind") == "detail" and v.get("boundary"):
            bc, br = v["boundary"]["center"], v["boundary"]["radius"]  # thin boundary of the enlarged area [03 rule 5]
            out.append(f'<circle cx="{bc[0]:.2f}" cy="{H - bc[1]:.2f}" r="{br:.2f}" stroke-width="{HATCH_LINE}"/>')
        if v.get("kind") in ("section", "detail") or (v.get("caption")):
            bb = v.get("bbox")
            label = v.get("caption") or v.get("section_symbol") or v.get("reference") or ""
            if bb and v.get("boundary"):  # caption under the detail's boundary circle
                bc, br = v["boundary"]["center"], v["boundary"]["radius"]
                bb = [min(bb[0], bc[0] - br), min(bb[1], bc[1] - br), max(bb[2], bc[0] + br), bb[3]]
            if bb and label:
                lx, ly = v.get("caption_position", [(bb[0] + bb[2]) / 2, bb[1] - 8])
                out.append(f'<text x="{lx:.2f}" y="{H - ly:.2f}" font-size="{LABEL_SIZE}" '
                           f'text-anchor="middle" fill="#000" stroke="none" font-family="osifont">{_esc(label)}</text>')
    out.append("</g>")
    out.append(f'<g id="dimensions" fill="none" stroke="{DIM_COLOR}" stroke-width="{DIM_LINE}" stroke-linecap="butt">')
    for d in snap.get("dimensions", []):
        out.append(_dimension_svg(d, H))
    out.append("</g>")
    out.append('<g id="annotations" fill="#000" stroke="none" font-family="osifont">')
    for a in snap.get("annotations", []):
        bb = a.get("bbox")
        if not bb or not a.get("text"):
            continue
        size = a.get("size", 3.5)
        y = H - bb[3] + size
        for line in a["text"]:
            out.append(f'<text x="{bb[0]}" y="{y:.2f}" font-size="{size}">{_esc(line)}</text>')
            y += size * 1.5
    out.append("</g>")
    out.append(_highlights_svg(highlights or [], H))
    out.append("</svg>")
    return "\n".join(out)


# SolidWorks-like dimension style (ISO): thin lines, slim filled arrows,
# extension-line gap and overshoot, text centred above the dimension line.
DIM_COLOR = "#000000"
DIM_LINE = LINE_THIN
ARROW_L = 3.0
ARROW_W = 1.0
EXT_GAP = 1.0
EXT_OVER = 2.0


def _arrow(tip, direction, H):
    """Filled arrowhead with its tip at ``tip`` pointing along ``direction`` (page coords)."""
    import math

    dx, dy = direction
    L = math.hypot(dx, dy) or 1.0
    dx, dy = dx / L, dy / L
    bx, by = tip[0] - dx * ARROW_L, tip[1] - dy * ARROW_L
    px, py = -dy * ARROW_W / 2, dx * ARROW_W / 2
    pts = [(tip[0], tip[1]), (bx + px, by + py), (bx - px, by - py)]
    return '<polygon points="' + " ".join(f"{x:.3f},{H - y:.3f}" for x, y in pts) + f'" fill="{DIM_COLOR}" stroke="none"/>'


def _line(a, b, H):
    return f'<line x1="{a[0]:.3f}" y1="{H - a[1]:.3f}" x2="{b[0]:.3f}" y2="{H - b[1]:.3f}"/>'


def _text(tc, text, H, vertical=False, anchor="middle"):
    rot = f' transform="rotate(-90 {tc[0]:.3f} {H - tc[1]:.3f})"' if vertical else ""
    return (f'<text x="{tc[0]:.3f}" y="{H - tc[1] + DIM_FONT_SIZE * 0.36:.3f}" font-size="{DIM_FONT_SIZE}" '
            f'text-anchor="{anchor}" fill="{DIM_COLOR}" stroke="none" font-family="osifont"{rot}>{_esc(text)}</text>')


def _dimension_svg(d, H):
    import math

    t = d.get("type")
    lines = d.get("lines") or []
    tc = d.get("text_center")
    tb = d.get("text_box")
    text = d.get("text", "")
    parts = []
    if t == "Angle" and d.get("angle_geometry"):
        points, extensions = angular_ink(d)
        for a, b in zip(points, points[1:]):
            parts.append(_line(a, b, H))
        for seg in extensions:
            parts.append(_line(seg[:2], seg[2:], H))
        for tip, neighbour in ((points[0], points[1]), (points[-1], points[-2])):
            length = math.dist(tip, neighbour)
            parts.append(_arrow(tip, ((tip[0] - neighbour[0]) / length, (tip[1] - neighbour[1]) / length), H))
        parts.append(_text(tc, text, H))
    elif t in ("DistanceX", "DistanceY") and len(lines) >= 3 and tc:
        k = 0 if t == "DistanceX" else 1  # measured axis index
        o = 1 - k
        (p1, q1), (p2, q2) = (lines[1][0:2], lines[1][2:4]), (lines[2][0:2], lines[2][2:4])
        level = q1[o]
        for p, q in ((p1, q1), (p2, q2)):
            sgn = 1.0 if level >= p[o] else -1.0
            if abs(level - p[o]) > EXT_GAP + 0.2:
                a = list(p); a[o] = p[o] + sgn * EXT_GAP
                b = list(q); b[o] = level + sgn * EXT_OVER
                parts.append(_line(a, b, H))
        lo, hi = sorted((q1[k], q2[k]))
        span = hi - lo
        ext_lo, ext_hi = lo, hi
        if span >= 2 * ARROW_L + 1.0:
            a_lo, a_hi = (lo, -1.0), (hi, 1.0)  # tips on the extension lines, pointing outwards
        else:
            a_lo, a_hi = (lo, 1.0), (hi, -1.0)  # short: arrows outside pointing inwards
            ext_lo, ext_hi = lo - ARROW_L - 2.0, hi + ARROW_L + 2.0
        if tb:  # run the dimension line under text that sits outside the span
            ext_lo = min(ext_lo, tb[k] - 0.5)
            ext_hi = max(ext_hi, tb[k + 2] + 0.5)
        def pt(v):
            p = [0.0, 0.0]; p[k] = v; p[o] = level
            return p
        parts.append(_line(pt(ext_lo), pt(ext_hi), H))
        for pos, sgn in (a_lo, a_hi):
            dvec = [0.0, 0.0]; dvec[k] = sgn
            parts.append(_arrow(pt(pos), dvec, H))
        parts.append(_text(tc, text, H, vertical=(t == "DistanceY")))
    elif t in ("Diameter", "Radius") and d.get("arc") and tc and tb:
        c = d["arc"]["center"]
        r = d["arc"]["radius"]
        right = tc[0] >= c[0]
        shoulder_y = tb[1] - 0.4
        s0 = (tb[0] - 0.8, shoulder_y) if right else (tb[2] + 0.8, shoulder_y)
        s1 = (tb[2] + 0.5, shoulder_y) if right else (tb[0] - 0.5, shoulder_y)
        ang = math.atan2(s0[1] - c[1], s0[0] - c[0])
        tip = (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))
        strokes = dimension_ink(d)
        if strokes:
            tip = strokes[0][:2]
            s0, s1 = strokes[0][2:], strokes[1][2:]
            ang = math.atan2(s0[1] - tip[1], s0[0] - tip[0])
        parts.append(_line(tip, s0, H))
        parts.append(_line(s0, s1, H))
        inward = (-math.cos(ang), -math.sin(ang))
        parts.append(_arrow(tip, inward, H))
        parts.append(_text(((tb[0] + tb[2]) / 2, tc[1]), text, H))
    else:
        for seg in lines:
            parts.append(_line(seg[0:2], seg[2:4], H))
        if tc:
            parts.append(_text(tc, text, H))
    return "".join(parts)


# Sections and details [03][01]: hatching type B thin at 45 deg, cutting plane
# type H (thin long-dash dot, thick ends) with arrows and letters, thin
# detail circle; view labels a little larger than dimension text.
HATCH_LINE = 0.25
HATCH_STEP = 3.0
CUT_LINE = 0.25
CUT_THICK = 0.7
CUT_END = 6.0  # mm of thick line at each end of the cutting plane
CUT_DASH = "12,1.5,1,1.5"
CUT_ARROW_L = 4.0
CUT_ARROW_W = 1.6
DETAIL_LINE = 0.35
LABEL_SIZE = 5.0


def _hatch_svg(loops, H, cid):
    """45 deg hatch clipped to the cut faces (even-odd, so holes stay open)."""
    import math

    pts = [(loop[i], loop[i + 1]) for loop in loops for i in range(0, len(loop) - 1, 2)]
    if not pts:
        return ""
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    # spacing proportional to the area: 2-3 mm, finer on narrow cuts [03 rule 2]
    step = max(1.5, min(HATCH_STEP, 0.25 * min(x1 - x0, y1 - y0) if min(x1 - x0, y1 - y0) > 0 else HATCH_STEP))
    # QtSvg has no clipPath: clip each hatch line against the loops (even-odd) here.
    edges = []
    for loop in loops:
        n = len(loop) // 2
        for i in range(n):
            a = (loop[2 * i], loop[2 * i + 1])
            b = (loop[2 * ((i + 1) % n)], loop[2 * ((i + 1) % n) + 1])
            if a != b:
                edges.append((a, b))
    parts = [f'<g class="hatch" stroke-width="{HATCH_LINE}" stroke-linecap="butt">']
    # lines y = x + c in page coordinates (45 deg, rising to the right)
    c = (y0 - x1) + 0.5 * step
    while c < (y1 - x0):
        ts = []
        for (ax, ay), (bx, by) in edges:
            fa, fb = ay - ax - c, by - bx - c  # signed distance (scaled) to the line
            if (fa > 0) == (fb > 0) or fa == fb:
                continue
            k = fa / (fa - fb)
            ts.append(ax + (bx - ax) * k)  # x of the crossing
        ts.sort()
        for i in range(0, len(ts) - 1, 2):
            xa, xb = ts[i], ts[i + 1]
            if xb - xa > 1e-3:
                parts.append(f'<line x1="{xa:.2f}" y1="{H - (xa + c):.2f}" x2="{xb:.2f}" y2="{H - (xb + c):.2f}"/>')
        c += step * math.sqrt(2)
    parts.append("</g>")
    return "".join(parts)


def _cut_arrow(tip, tail, H):
    import math

    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    bx, by = tip[0] - ux * CUT_ARROW_L, tip[1] - uy * CUT_ARROW_L
    px, py = -uy * CUT_ARROW_W / 2, ux * CUT_ARROW_W / 2
    poly = [(tip[0], tip[1]), (bx + px, by + py), (bx - px, by - py)]
    return (f'<line x1="{tail[0]:.2f}" y1="{H - tail[1]:.2f}" x2="{bx:.2f}" y2="{H - by:.2f}" stroke-width="{CUT_LINE}"/>'
            '<polygon points="' + " ".join(f"{x:.2f},{H - y:.2f}" for x, y in poly) + '" fill="#000" stroke="none"/>')


def _cutting_line_svg(cl, H):
    """ISO 128 type H cutting plane on the base view: thin chain, thick ends, arrows, letters."""
    import math

    pts = cl.get("points") or []
    if len(pts) < 2:
        return ""
    parts = ['<g class="cutting-plane" fill="none" stroke="#000" stroke-linecap="butt">']
    poly = " ".join(f"{p[0]:.2f},{H - p[1]:.2f}" for p in pts)
    parts.append(f'<polyline points="{poly}" stroke-width="{CUT_LINE}" stroke-dasharray="{CUT_DASH}"/>')

    def thick(a, b, length=CUT_END):
        L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        k = min(length, L) / L
        q = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        parts.append(f'<line x1="{a[0]:.2f}" y1="{H - a[1]:.2f}" x2="{q[0]:.2f}" y2="{H - q[1]:.2f}" stroke-width="{CUT_THICK}"/>')

    thick(pts[0], pts[1])
    thick(pts[-1], pts[-2])
    for i in range(1, len(pts) - 1):  # change of direction (half / offset section): thick corner
        thick(pts[i], pts[i - 1], CUT_END / 2)
        thick(pts[i], pts[i + 1], CUT_END / 2)
    for a in cl.get("arrows", []):
        parts.append(_cut_arrow(a["tip"], a["tail"], H))
    parts.append("</g>")
    for lt in cl.get("letters", []):
        x, y = lt["at"]
        parts.append(f'<text x="{x:.2f}" y="{H - y + LABEL_SIZE * 0.36:.2f}" font-size="{LABEL_SIZE}" text-anchor="middle" '
                     f'fill="#000" stroke="none" font-family="osifont">{_esc(lt["text"])}</text>')
    return "".join(parts)


def _detail_circle_svg(dc, letter, H):
    c, r = dc["center"], dc["radius"]
    x, y = dc.get("letter_at", (c[0] + r + 4, c[1] + r + 4))
    return (f'<circle cx="{c[0]:.2f}" cy="{H - c[1]:.2f}" r="{r:.2f}" fill="none" stroke="#000" stroke-width="{DETAIL_LINE}"/>'
            f'<text x="{x:.2f}" y="{H - y + LABEL_SIZE * 0.36:.2f}" font-size="{LABEL_SIZE}" text-anchor="middle" '
            f'fill="#000" stroke="none" font-family="osifont">{_esc(letter)}</text>')


def _highlights_svg(highlights, H, scale=1.0):
    parts = ['<g id="inspection" fill="none" stroke-width="0.6">']
    for h in highlights:
        bb = h.get("bbox")
        if not bb:
            continue
        col = COLORS.get(h.get("severity"), COLORS["review"])
        x0, y0, x1, y1 = bb[0] - 1, H - bb[3] - 1, bb[2] + 1, H - bb[1] + 1
        parts.append(f'<rect x="{x0 * scale:.2f}" y="{y0 * scale:.2f}" width="{(x1 - x0) * scale:.2f}" height="{(y1 - y0) * scale:.2f}" stroke="{col}" stroke-dasharray="2,1"/>')
        label = _esc(h.get("label", ""))
        if label:
            parts.append(f'<text x="{x0 * scale:.2f}" y="{(y0 - 0.8) * scale:.2f}" font-size="{3.2 * scale:.2f}" fill="{col}" stroke="none" font-family="osifont" font-weight="bold">{label}</text>')
    parts.append("</g>")
    return "".join(parts)


def techdraw_svg(page, highlights=None):
    """TechDraw's own export with highlights appended (GUI only)."""
    import TechDrawGui

    fd, path = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    try:
        TechDrawGui.exportPageAsSvg(page, path)
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    W, H = float(page.PageWidth), float(page.PageHeight)
    m = re.search(r'viewBox="([^"]+)"', svg)
    scale = 1.0
    if m:
        vb = [float(v) for v in m.group(1).replace(",", " ").split()]
        if len(vb) == 4 and vb[2] > 0:
            scale = vb[2] / W
    overlay = _highlights_svg(highlights or [], H, scale)
    return svg.replace("</svg>", overlay + "</svg>") if "</svg>" in svg else svg


def _qt():
    if not App.GuiUp:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6 import QtCore, QtGui, QtSvg
    except ImportError:  # FreeCAD builds with PySide2
        from PySide2 import QtCore, QtGui, QtSvg
    if QtGui.QGuiApplication.instance() is None:
        _qt.app = QtGui.QGuiApplication(["free-drawing", "-platform", "offscreen"])
    if not getattr(_qt, "fonts_loaded", False):
        # The offscreen platform has no system fonts; load TechDraw's ISO 3098 font.
        res = App.getResourceDir()
        for rel in (("Mod", "TechDraw", "Resources", "fonts", "osifont-lgpl3fe.ttf"),
                    ("examples", "osifont-lgpl3fe.ttf")):
            path = os.path.join(res, *rel)
            if os.path.isfile(path):
                QtGui.QFontDatabase.addApplicationFont(path)
                break
        _qt.fonts_loaded = True
    return QtCore, QtGui, QtSvg


def svg_to_png(svg, width_px=1600, out_path=None):
    QtCore, QtGui, QtSvg = _qt()
    data = QtCore.QByteArray(svg.encode("utf-8"))
    r = QtSvg.QSvgRenderer(data)
    if not r.isValid():
        raise RuntimeError("SVG could not be rendered")
    vb = r.viewBoxF()
    aspect = vb.height() / vb.width() if vb.width() > 0 else 297 / 420
    img = QtGui.QImage(int(width_px), int(width_px * aspect), QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("white"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    r.render(p)
    p.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())
    if out_path:
        with open(out_path, "wb") as f:
            f.write(png)
    return png


def svg_to_pdf(svg, out_path, width_mm, height_mm):
    QtCore, QtGui, QtSvg = _qt()
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    writer = QtGui.QPdfWriter(out_path)
    writer.setPageSize(QtGui.QPageSize(QtCore.QSizeF(width_mm, height_mm), QtGui.QPageSize.Millimeter))
    writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
    writer.setResolution(600)
    p = QtGui.QPainter(writer)
    r.render(p)
    p.end()
    return out_path


def page_svg(doc, page, snap, highlights=None, source="auto"):
    if source == "techdraw" or (source == "auto" and App.GuiUp):
        try:
            return techdraw_svg(page, highlights), "techdraw"
        except Exception:
            if source == "techdraw":
                raise
    tpl = page.Template
    tpl_path = str(tpl.Template) if tpl is not None else None
    return schematic_svg(snap, highlights, tpl_path), "schematic"


def png_base64(png):
    return base64.b64encode(png).decode("ascii")
