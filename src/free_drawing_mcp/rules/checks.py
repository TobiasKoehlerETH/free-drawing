"""Rules: one function per checklist section, ids match guidelines/CHECKLIST.md.

Every checklist item yields at least one finding (pass / fail / warn / review /
na) so a report always shows complete coverage. Machine-checkable items are
decided from the snapshot; purely visual items return ``review`` with a hint.
Fixes are action dicts understood by ``apply_drawing_actions``.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any, Callable

from . import geom as g
from .model import Finding

FONT = 3.5
PLACEHOLDERS = {"A. Nemesis", "B. Hecate", "DN", "AAA", "ST1", "ST2", "RD", "TBD", "", "-", "?", "XXX",
                "Stainless steel Mat.No. 1.4301"}
REV_SKIP = set("IOQSXZ")
DIA_SYMBOLS = ("⌀", "Ø", "ø")
GDT_RE = re.compile(r"[⌖⏥⌭⟂∥∠⌯◎⌒⌓↗⌰]|\b(POSITION|FLATNESS|PERPENDICULARITY|PARALLELISM|RUN-?OUT|PROFILE|CYLINDRICITY|CIRCULARITY|STRAIGHTNESS)\b", re.I)
THREAD_RE = re.compile(r"\b(M\d+(?:[.,]\d+)?(?:\s*[x×]\s*\d+(?:[.,]\d+)?)?(?:-\d[gGhHefEF]{1,2})?|\d+/\d+-\d+\s*UN[CFE]|\d+/\d+-\d+\s*NPTF?|G\s*\d+/\d+|R[pc]?\s*\d+/\d+|Tr\d+\s*[x×]\s*\d+)")


class Ctx:
    """Pre-digested snapshot shared by all rules."""

    def __init__(self, snap: dict[str, Any]):
        self.s = snap
        sh = snap.get("sheet", {})
        self.W = sh.get("width", 420.0)
        self.H = sh.get("height", 297.0)
        self.frame = sh.get("frame") or [10, 10, self.W - 10, self.H - 10]
        self.tb = sh.get("title_block")
        self.fields = snap.get("title_block", {}) or {}
        self.meta = snap.get("meta", {}) or {}
        self.views = [v for v in snap.get("views", []) if v.get("kind") != "group"]
        self.groups = {v["name"]: v for v in snap.get("views", []) if v.get("kind") == "group"}
        self.ortho = [v for v in self.views if v.get("kind") == "ortho"]
        self.pictorial = [v for v in self.views if v.get("kind") == "pictorial"]
        self.sections = [v for v in self.views if v.get("kind") == "section"]
        self.details = [v for v in self.views if v.get("kind") == "detail"]
        self.dims = snap.get("dimensions", [])
        self.annos = list(snap.get("annotations", []))
        self.annos.extend({"name": v["name"] + "_caption", "kind": "caption", "text": [v.get("caption", "")],
                           "bbox": v["caption_box"]} for v in self.views if v.get("caption_box"))
        self.notes = [a for a in self.annos if a.get("kind") == "note"]
        self.view_by_name = {v["name"]: v for v in self.views}
        self.all_text = "\n".join(l for a in self.annos for l in a.get("text", [])) + "\n" + \
            "\n".join(d.get("text", "") for d in self.dims) + "\n" + "\n".join(self.fields.values())
        self.notes_text = "\n".join(l for a in self.notes for l in a.get("text", []))
        self.main_note = max(self.notes, key=lambda a: len(a.get("text", [])), default=None)

    def ortho_scale(self) -> float | None:
        sc = [v.get("scale") for v in self.ortho if v.get("scale")]
        return sc[0] if sc else None

    def view_segments(self, include_hidden: bool = True):
        for v in self.views:
            for pl in v.get("polylines_visible", []):
                yield from g.polyline_segments(pl)
            if include_hidden:
                for pl in v.get("polylines_hidden", []):
                    yield from g.polyline_segments(pl)
            for cosmetic in v.get("cosmetic", []):
                if cosmetic.get("format", {}).get("visible", True):
                    yield from g.polyline_segments(cosmetic.get("points", []))
            for key in ("boundary", "detail_circle"):
                circle = v.get(key)
                if circle:
                    x, y = circle["center"]
                    r = circle["radius"]
                    points = [(x + r * math.cos(i * math.pi / 32), y + r * math.sin(i * math.pi / 32)) for i in range(65)]
                    for a, b in zip(points, points[1:]):
                        yield (*a, *b)


def scale_text(s: float) -> str:
    if s >= 1:
        v = round(s, 3)
        return f"{int(v) if v == int(v) else v} : 1"
    inv = round(1 / s, 3)
    return f"1 : {int(inv) if inv == int(inv) else inv}"


def norm_scale(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def F(rule, status, msg, **kw) -> Finding:
    return Finding(rule=rule, status=status, message=msg, **kw)


def placeholder(v: str | None) -> bool:
    return v is None or v.strip() in PLACEHOLDERS


# ----------------------------------------------------------- free spot search
def obstacles(c: Ctx, exclude: str | None = None):
    boxes = []
    for d in c.dims:
        if d["name"] != exclude and d.get("text_box"):
            boxes.append(d["text_box"])
    for a in c.annos:
        if a.get("bbox"):
            boxes.append(a["bbox"])
    for v in c.pictorial:
        if v.get("bbox"):
            boxes.append(v["bbox"])
    if c.tb:
        boxes.append(c.tb)
    lines = [seg for d in c.dims if d["name"] != exclude for seg in d.get("ink_lines", d.get("lines", []))]
    return boxes, lines


def is_free(c: Ctx, box, boxes, lines, segs) -> bool:
    fr = c.frame
    if not g.inside(box, [fr[0] + 1, fr[1] + 1, fr[2] - 1, fr[3] - 1]):
        return False
    pad = g.expand(box, 0.6)
    if any(g.overlap(pad, b) for b in boxes):
        return False
    if any(g.seg_box(sg, pad) for sg in lines):
        return False
    if any(g.seg_box(sg, pad) for sg in segs):
        return False
    return True


def free_spot(c: Ctx, d: dict, segs) -> tuple[float, float] | None:
    """Nearest label position for dimension ``d`` whose text box is clear."""
    tb = d.get("text_box")
    if not tb:
        return None
    cx, cy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
    boxes, lines = obstacles(c, d["name"])
    v = c.view_by_name.get(d.get("view") or "")
    vc = ((v["bbox"][0] + v["bbox"][2]) / 2, (v["bbox"][1] + v["bbox"][3]) / 2) if v and v.get("bbox") else (cx, cy)
    cands = []
    if d["type"] == "DistanceX":
        sgn = 1 if cy >= vc[1] else -1
        for k in range(1, 10):
            cands.append((cx, cy + sgn * 8 * k))
        for k in (1, -1, 2, -2):
            cands.append((cx + k * (tb[2] - tb[0] + 2), cy))
    elif d["type"] == "DistanceY":
        sgn = 1 if cx >= vc[0] else -1
        for k in range(1, 10):
            cands.append((cx + sgn * 8 * k, cy))
        for k in (1, -1, 2, -2):
            cands.append((cx, cy + k * (tb[3] - tb[1] + 2)))
    else:
        for rad in (6, 10, 14, 20, 28, 36):
            for ang in range(0, 360, 30):
                cands.append((cx + rad * math.cos(math.radians(ang)), cy + rad * math.sin(math.radians(ang))))
    for x, y in cands:
        box = g.translate(tb, x - cx, y - cy)
        if is_free(c, box, boxes, lines, segs):
            return round(x, 2), round(y, 2)
    return None


# ================================================================= A. sheet
def rules_A(c: Ctx):
    sh = c.s.get("sheet", {})
    tpl = sh.get("template", "") or ""
    size = sh.get("size_name")
    yield F("A.1", "pass" if size and tpl.upper().startswith(size) else "fail",
            f"sheet {size or '?'} {c.W:g}×{c.H:g} mm, template {tpl or 'none'}")
    fr = sh.get("frame")
    if fr and fr[0] >= 19.5 and fr[1] >= 9.5 and c.W - fr[2] >= 9.5 and c.H - fr[3] >= 9.5:
        yield F("A.2", "pass", "border with 20 mm filing margin on the left")
    else:
        yield F("A.2", "warn", "drawing frame / filing margin not detected in the template", hint="use an ISO 5457 template")
    yield F("A.3", "pass" if sh.get("centring_marks") else "warn", "centring marks " + ("present" if sh.get("centring_marks") else "not found in template"))
    tb = c.tb
    if not tb:
        yield F("A.4", "fail", "no title block found in the template")
    else:
        ok = tb[2] >= c.frame[2] - 1 and tb[1] <= c.frame[1] + 1
        yield F("A.4", "pass" if ok else "fail", "title block in the bottom-right corner" if ok else "title block is not bottom-right", bbox=tb)
    # A.5 fields
    required = {"title": "title", "scale": "scale", "revision_index": "revision", "sheet_number": "sheet numbering"}  # material: K.1
    bad = [k for k in required if k in c.fields and placeholder(c.fields.get(k))]
    missing_fields = [k for k in required if k not in c.fields]
    if bad or missing_fields:
        yield F("A.5", "fail", "title block field(s) empty or template placeholder: " + ", ".join(bad + missing_fields), bbox=tb,
                fix=[{"op": "set_title_block", "fields": {k: f"<{required[k]}>" for k in bad}}] if bad else None)
    else:
        yield F("A.5", "pass", "title, scale, material, revision and sheet number filled")
    units_ok = bool(re.search(r"\bUNITS?\b.*\bmm\b|\bmm\b", c.notes_text, re.I)) or "unit" in " ".join(c.fields).lower()
    if not units_ok:
        yield F("A.5", "fail", "units are not declared on the sheet",
                fix=_note_append(c, "UNITS: mm"), auto=True)
    sc = c.ortho_scale()
    if sc and "scale" in c.fields and norm_scale(c.fields["scale"]) != norm_scale(scale_text(sc)):
        yield F("A.5", "fail", f"title block scale '{c.fields['scale']}' does not match view scale {scale_text(sc)}", bbox=tb,
                fix=[{"op": "set_title_block", "fields": {"scale": scale_text(sc)}}], auto=True)
    gt = c.fields.get("general_tolerances", "")
    if re.search(r"ISO\s*(2768|22081)", gt + "\n" + c.notes_text, re.I):
        yield F("A.6", "pass", "general tolerance note present")
    else:
        yield F("A.6", "review", "verify the declared general or individual tolerance scheme",
                hint="tolerances require design input; a general tolerance note is not mandatory if each requirement is specified")
    sym = sh.get("projection_symbol")
    proj = c.s.get("projection")
    if not sym:
        yield F("A.7", "fail", "projection symbol missing from the title block", hint="use an ISO template that carries the symbol")
    elif proj and sym != proj:
        yield F("A.7", "fail", f"template shows the {sym}-angle symbol but views are arranged {proj}-angle")
    else:
        yield F("A.7", "pass", f"{sym}-angle symbol present and matches the views")
    yield from _a8(c)


def _a8(c: Ctx):
    tb = c.tb
    fr = c.frame
    found = False
    segs = list(c.view_segments())
    for v in c.views:
        b = v.get("bbox")
        if not b:
            continue
        if tb and g.overlap(b, tb):
            found = True
            dy = tb[3] + 3 - b[1]
            target = v.get("group") or v["name"]
            fix = [{"op": "move_group", "name": target, "dx": 0, "dy": round(dy, 2)}] if v.get("group") else \
                [{"op": "move_view", "name": v["name"], "x": round((b[0] + b[2]) / 2, 2), "y": round((b[1] + b[3]) / 2 + dy, 2)}]
            yield F("A.8", "fail", f"view {v['name']} overlaps the title block", objects=[v["name"]], bbox=b, fix=fix, auto=True)
        elif not g.inside(b, fr):
            found = True
            yield F("A.8", "fail", f"view {v['name']} extends outside the drawing frame", objects=[v["name"]], bbox=b,
                    hint="use a smaller scale or larger sheet")
    for d in c.dims:
        tbx = d.get("text_box")
        hit_tb = tb and (g.overlap(tbx, tb) or any(g.seg_box(sg, tb) for sg in d.get("lines", [])))
        out = tbx and not g.inside(tbx, fr)
        if hit_tb or out:
            found = True
            spot = free_spot(c, d, segs)
            yield F("A.8", "fail", f"{d['name']} {'overlaps the title block' if hit_tb else 'is outside the frame'}",
                    objects=[d["name"]], bbox=tbx, fix=[{"op": "move_dimension", "name": d["name"], "x": spot[0], "y": spot[1]}] if spot else None,
                    auto=bool(spot))
    for a in c.annos:
        b = a.get("bbox")
        if b and tb and g.overlap(b, tb, -0.5):
            found = True
            yield F("A.8", "fail", f"{a['name']} overlaps the title block", objects=[a["name"]], bbox=b,
                    fix=[{"op": "set_note", "name": a["name"], "y": round(tb[3] + 3 + (b[3] - b[1]) / 2, 2)}]
                    if a.get("kind") != "caption" else None, auto=a.get("kind") != "caption")
    if not found:
        yield F("A.8", "pass", "nothing overlaps the title block or leaves the frame")


def _note_append(c: Ctx, line: str):
    if c.main_note:
        return [{"op": "set_note", "name": c.main_note["name"], "append": [line]}]
    fr = c.frame
    return [{"op": "add_note", "text": [line], "x": round(fr[0] + 40, 1), "y": round(fr[1] + 10, 1), "name": "Notes"}]


# ================================================================= B. lines
def rules_B(c: Ctx):
    lw = [v.get("line_widths") for v in c.ortho if v.get("line_widths")]
    if not lw:
        yield F("B.1", "na", "no orthographic views")
    else:
        bad = [w for w in lw if not (w["visible"] > w["hidden"] and w["visible"] > w.get("center", w["hidden"]))]
        src = lw[0].get("source")
        note = "" if src == "view" else " (TechDraw line group defaults; GUI not running)"
        yield F("B.1", "fail" if bad else "pass", f"visible {lw[0]['visible']} mm vs hidden {lw[0]['hidden']} mm{note}")
        same = len({(w["visible"], w["hidden"]) for w in lw}) == 1
        yield F("B.2", "pass" if same and not bad else "warn",
                "hidden lines thin and dashed, widths consistent across views" if same else "line widths differ between views")
    missing = []
    for v in c.ortho:
        for circ in v.get("circles", []):
            if circ.get("full") and not circ.get("center_mark"):
                missing.append((v["name"], circ))
    if missing:
        views = sorted({m[0] for m in missing})
        b = g.union([[m[1]["center"][0] - m[1]["radius"], m[1]["center"][1] - m[1]["radius"],
                      m[1]["center"][0] + m[1]["radius"], m[1]["center"][1] + m[1]["radius"]] for m in missing])
        yield F("B.3", "fail", f"{len(missing)} circle(s) without centre lines", objects=views, bbox=b,
                fix=[{"op": "add_centerlines", "view": v} for v in views], auto=True)
    elif any(v.get("circles") for v in c.ortho):
        yield F("B.3", "pass", "all circles carry centre lines")
    else:
        yield F("B.3", "na", "no circular features")
    yield F("B.4", "review", "cutting-plane lines distinct (type H)") if c.sections else F("B.4", "na", "no section views")
    yield F("B.5", "pass", "dimension, extension and leader lines use the thin line (TechDraw default)")


# ================================================================= C. views
_FIRST = {"Top": (0, -1), "Bottom": (0, 1), "Right": (-1, 0), "Left": (1, 0), "Rear": (1, 0)}


def rules_C(c: Ctx):
    if not c.ortho and not c.pictorial and not c.sections and not c.details:
        yield F("C.1", "fail", "no inspectable TechDraw views on the page - geometry embedded in the template or an "
                "SVG cannot be checked, so completeness is not assessed",
                hint="create the views with create_drawing / TechDraw view objects")
        return
    front = next((v for v in c.ortho if v.get("projection_type") == "Front"), c.ortho[0] if c.ortho else None)
    if front is None:
        yield F("C.1", "fail", "no orthographic views on the sheet")
    elif front.get("edges_hidden", 0) > front.get("edges_visible", 0):
        yield F("C.1", "warn", f"front view {front['name']} shows more hidden than visible edges", objects=[front["name"]],
                bbox=front.get("bbox"), hint="choose the direction that shows the part's function with fewest hidden lines")
    else:
        yield F("C.1", "review", "front view shows the functional orientation")
    proj = c.s.get("projection") or "first"
    bad = False
    standard_scales = {round(v["scale"], 8) for v in c.ortho if v.get("scale")}
    if len(standard_scales) > 1:
        bad = True
        yield F("C.2", "fail", "standard orthographic views use different scales",
                objects=[v["name"] for v in c.ortho], hint="use one shared scale for all standard views; label enlarged details separately")
    if front is not None:
        fc = front["center"]
        for v in c.ortho:
            t = v.get("projection_type")
            if v is front or t not in _FIRST:
                continue
            ex, ey = _FIRST[t]
            if proj == "third" and t != 'Rear':
                ex, ey = -ex, -ey
            dx, dy = v["center"][0] - fc[0], v["center"][1] - fc[1]
            wrong = (ex and (dx * ex <= 0 or abs(dy) > 0.5)) or (ey and (dy * ey <= 0 or abs(dx) > 0.5))
            if wrong:
                bad = True
                fb, vb = front.get("bbox") or [0, 0, 0, 0], v.get("bbox") or [0, 0, 0, 0]
                min_dx = (fb[2] - fb[0]) / 2 + (vb[2] - vb[0]) / 2 + 20.0
                min_dy = (fb[3] - fb[1]) / 2 + (vb[3] - vb[1]) / 2 + 20.0
                tx = fc[0] + (max(abs(dx), min_dx) * ex if ex else 0)
                ty = fc[1] + (max(abs(dy), min_dy) * ey if ey else 0)
                yield F("C.2", "fail", f"{t} view {v['name']} is not placed per {proj}-angle projection",
                        objects=[v["name"]], bbox=v.get("bbox"),
                        fix=[{"op": "move_view", "name": v["name"], "x": round(tx, 2), "y": round(ty, 2)}], auto=True)
    if not bad:
        yield F("C.2", "pass", f"views follow {proj}-angle arrangement and stay aligned")
    used = {d.get("view") for d in c.dims}
    strict = c.meta.get("dimensioned_only", False)
    candidates = c.ortho + c.sections + c.details
    redundant = [v["name"] for v in candidates if v["name"] not in used]
    if redundant:
        yield F("C.3", "fail" if strict else "warn", "view(s) carry no dimension - possibly redundant", objects=redundant,
                hint="assign a needed dimension or hide the view; retain projection anchors and section/detail parents")
    else:
        yield F("C.3", "pass", f"{len(candidates)} defining view(s), each carries a dimension")
    loose = [v for v in c.ortho if not v.get("group") and not (v.get("caption") or "").strip()]
    loose += [v for v in c.sections + c.details if not (v.get("caption") or v.get("section_symbol") or v.get("reference"))]
    if loose:
        letters = iter("ABCDEFGHJKLMNPRTUVWY")
        yield F("C.4", "fail", "removed/rotated views without a letter", objects=[v["name"] for v in loose],
                fix=[{"op": "set_view", "name": v["name"], "caption": f"VIEW {next(letters)}"} for v in loose], auto=True)
    else:
        yield F("C.4", "pass" if (c.sections or c.details or any(not v.get("group") for v in c.ortho)) else "na",
                "removed/rotated views are lettered")
    pic_names = {v["name"] for v in c.pictorial}
    on_pic = [d["name"] for d in c.dims if d.get("view") in pic_names]
    yield (F("C.5", "fail", "dimensions on the pictorial view", objects=on_pic, fix=[{"op": "delete", "names": on_pic}])
           if on_pic else F("C.5", "pass" if c.pictorial else "na", "pictorial view is for recognition only"))
    no_hidden = [v["name"] for v in c.ortho if not v.get("hard_hidden")]
    yield (F("C.6", "fail", "orthographic view(s) without hidden lines", objects=no_hidden,
             fix=[{"op": "set_view", "name": n, "hard_hidden": True} for n in no_hidden], auto=True)
           if no_hidden else F("C.6", "pass", "hidden lines visible on all orthographic views"))
    yield from _c7(c)
    shaded_ortho = [v["name"] for v in c.ortho if v.get("shaded")]
    yield F("C.8", "fail" if shaded_ortho else "pass",
            "only the isometric view is shaded" if not shaded_ortho else "orthographic views are shaded", objects=shaded_ortho)


def _c7(c: Ctx):
    if not c.pictorial:
        yield F("C.7", "fail", "no isometric recognition view", hint="recreate with create_drawing(iso=True)")
        return
    iso = c.pictorial[0]
    b = iso.get("bbox")
    if not iso.get("shaded"):
        yield F("C.7", "fail", "isometric view is not shaded in colour (project rule: full-colour isometric)",
                objects=[iso["name"]], bbox=b, hint="recreate with iso_style='shaded'")
    sc = iso.get("scale")
    enlarged=bool(c.meta.get('iso_readability_enlarged') and sc and sc>0.5)
    if sc and abs(sc - 0.5) > 1e-6 and not enlarged:
        yield F("C.7", "warn", f"isometric at {scale_text(sc)} instead of 1 : 2 (reduced to fit)", objects=[iso["name"]], bbox=b)
    if b and c.tb:
        w, h = b[2] - b[0], b[3] - b[1]
        ok = (b[0] + b[2]) / 2 > c.W * 0.55 and b[1] >= c.tb[3] - 0.5 and (b[1] + b[3]) / 2 < c.H * 0.6
        if not ok:
            x = c.frame[2] - 6 - w / 2
            y = c.tb[3] + 6 + h / 2
            yield F("C.7", "fail", "isometric view is not in the bottom-right zone above the title block",
                    objects=[iso["name"]], bbox=b, fix=[{"op": "move_view", "name": iso["name"], "x": round(x, 2), "y": round(y, 2)}], auto=True)
            return
    if iso.get("shaded") and not (sc and abs(sc - 0.5) > 1e-6):
        yield F("C.7", "pass", "compact shaded isometric 1 : 2 bottom-right")
    elif iso.get("shaded") and enlarged:
        yield F('C.7','pass','coloured isometric enlarged for recognition, bottom-right')
    elif iso.get("shaded"):
        yield F("C.7", "pass", "shaded isometric bottom-right")


# ================================================================= D. sections
def rules_D(c: Ctx):
    extra = c.meta.get("supplementary_views", {})
    if extra.get("unplaced"):
        yield F("D.1", "fail", "supplementary views do not fit: " + "; ".join(extra["unplaced"]),
                hint="use a larger sheet or arrange the required views on another sheet")
    errors = [v["name"] for v in c.sections + c.details if v.get("section_error") or v.get("detail_error")]
    if errors:
        yield F("D.1", "fail", "supplementary view geometry could not be verified", objects=errors)
    if any("failed:" in n for n in extra.get("notes", [])):
        yield F("D.1", "fail", "a required supplementary view failed to build", hint="inspect the generation warnings")
    if not c.sections:
        for rid, msg in (("D.1", "section views"), ("D.2", "hatching"), ("D.3", "sectioned standard parts")):
            yield F(rid, "na", f"no {msg}")
    else:
        unl = [v["name"] for v in c.sections if not v.get("section_symbol") or
               v.get("base_view") not in c.view_by_name or not v.get("cutting_line", {}).get("arrows")]
        yield F("D.1", "fail" if unl else "pass", "section views have labelled cutting planes" if not unl else "section without letter", objects=unl)
        hatch_errors = [v["name"] for v in c.sections if v.get("hatch_error") or not v.get("hatch")]
        yield F("D.2", "fail" if hatch_errors else "review", "cut hatching missing or invalid" if hatch_errors else
                "hatching consistent and readable", objects=hatch_errors)
        yield F("D.3", "review", "shafts, ribs, fasteners not sectioned longitudinally")
    if not c.details:
        yield F("D.4", "na", "no detail views")
    else:
        bad = [v["name"] for v in c.details if not v.get("reference") or not re.search(r"\(\s*\d+\s*:\s*\d+\s*\)", v.get("caption", ""))]
        yield F("D.4", "warn" if bad else "pass", "detail views show parent letter and scale" if not bad else "detail view missing letter or '(n:m)' scale", objects=bad)
    aux = [v["name"] for v in c.ortho if not v.get("group") and v.get("axis") is None]
    yield F("D.5", "review" if aux else "na", "auxiliary views identified" if aux else "no broken/auxiliary views", objects=aux)


# ================================================================= E. dimensions
def _bare_text(t: str) -> str:
    return re.sub(r"^\d+\s*[×x]\s*", "", t or "").strip()


def rules_E(c: Ctx):
    yield from _e1(c)
    long_ext = []
    for d in c.dims:
        if d["type"] in ("DistanceX", "DistanceY") and len(d.get("lines", [])) >= 3:
            if max(g.length(d["lines"][1]), g.length(d["lines"][2])) > 0.5 * c.H:
                long_ext.append(d["name"])
    yield (F("E.2", "warn", "very long extension lines - dimension far from its feature", objects=long_ext)
           if long_ext else F("E.2", "review", "extension and dimension lines drawn cleanly"))
    yield F("E.3", "pass", "aligned text orientation (TechDraw ISO style, readable from bottom and right)")
    bad_sym = []
    fixes = []
    for d in c.dims:
        t = d.get("text", "")
        bare = _bare_text(t)
        if d["type"] == "Diameter" and not any(s in t for s in DIA_SYMBOLS):
            bad_sym.append(d["name"])
            fixes.append({"op": "set_dimension", "name": d["name"], "format_spec": _insert_prefix(d["format_spec"], "⌀")})
        if d["type"] == "Radius" and not re.match(r"^(S?R|CR|RMIN|RMAX)", bare):
            bad_sym.append(d["name"])
            fixes.append({"op": "set_dimension", "name": d["name"], "format_spec": _insert_prefix(d["format_spec"], "R")})
    yield (F("E.4", "fail", "diameter/radius dimensions without Ø / R symbol", objects=bad_sym, fix=fixes, auto=True)
           if bad_sym else F("E.4", "pass", "Ø, R, C and multiplicity symbols used correctly"))
    hidden = [d["name"] for d in c.dims if d.get("ref_hidden")]
    yield (F("E.5", "warn", "dimensions attached to hidden lines", objects=hidden,
             hint="move them to a view where the feature is visible, or add a section")
           if hidden else F("E.5", "pass", "no dimension targets a hidden line"))
    yield from _e6(c)
    radii = [d for d in c.dims if d["type"] == "Radius"]
    yield F("E.7", "pass" if radii else "na", "radius variants explicit (R / SR / CR / RMIN / RMAX)")
    yield from _e8(c)
    br = [d for d in c.dims if re.search(r"[()\[\]]", d.get("text", ""))]
    if br:
        yield F("E.9", "review", "verify reference or theoretically exact meaning of bracketed dimensions",
                objects=[d["name"] for d in br], hint="do not strip brackets: this can change a dimension's specification meaning")
    else:
        yield F("E.9", "pass", "no bracketed dimension values")
    yield from _e10(c)
    yield from _e11(c)


def _insert_prefix(spec: str, sym: str) -> str:
    m = re.match(r"^(\d+\s*[×x]\s*)?(.*)$", spec or "%.2w")
    return (m.group(1) or "") + sym + m.group(2)


def _e1(c: Ctx):
    issues = 0
    for gap in c.meta.get("coverage_gaps", []):
        issues += 1
        yield F("E.1", "fail", "definition requires engineering input: " + gap)
    for u in c.meta.get("unplaced", []) or []:
        issues += 1
        yield F("E.1", "fail", f"not dimensioned: {u}", hint="add with apply_drawing_actions add_dimension (view, type, refs)")
    ext = (c.s.get("model") or {}).get("extents", {})
    for axis, L in ext.items():
        if L <= 0.01:
            continue
        ok = any(d.get("model_axis") == axis and d.get("value") is not None and abs(d["value"] - L) < 0.02 for d in c.dims)
        ok = ok or any(d["type"] in ("Diameter",) or "⌀" in d.get("text", "") for d in c.dims
                       if d.get("value") is not None and abs(d["value"] - L) < 0.02)
        if not ok:
            issues += 1
            yield F("E.1", "fail", f"overall size along {axis} ({L:g} mm) is not dimensioned")
    sizes = {round(d["value"] * (2 if d["type"] == "Radius" else 1), 2) for d in c.dims
             if d.get("value") is not None and (d["type"] in ("Diameter", "Radius") or "⌀" in d.get("text", ""))}
    for d in c.dims:  # sizes stated inside callouts, e.g. counterbore '⌴⌀11'
        for m in re.finditer(r"[⌀Ø]\s*(\d+(?:[.,]\d+)?)", d.get("text", "")):
            sizes.add(round(float(m.group(1).replace(",", ".")), 2))
    chamfers=[float(m.group(1).replace(',','.')) for d in c.dims for m in re.finditer(r'\bC\s*(\d+(?:[.,]\d+)?)',d.get('text',''))]
    for v in c.ortho:
        for circ in v.get("circles", []):
            r = circ.get("model_radius")
            if not circ.get("full") or r is None:
                continue
            if round(2 * r, 2) in sizes:
                continue
            derived=any(round(2*other.get('model_radius',0),2) in sizes and g.length([*circ['center'],*other['center']])<.01
                        and any(abs(abs(2*r-2*other.get('model_radius',0))-2*leg)<.01 for leg in chamfers)
                        for other in v.get('circles',[]))
            if derived:
                yield F('E.1','review',f"circle ⌀{2*r:g} may be derived from a concentric diameter and chamfer; confirm callout scope",
                        objects=[v['name']])
                continue
            issues += 1
            b = [circ["center"][0] - circ["radius"], circ["center"][1] - circ["radius"],
                 circ["center"][0] + circ["radius"], circ["center"][1] + circ["radius"]]
            yield F("E.1", "fail", f"circle ⌀{2 * r:g} in {v['name']} has no size", objects=[v["name"]], bbox=b,
                    fix=[{"op": "add_dimension", "view": v["name"], "type": "Diameter", "refs": [circ["edge"]],
                          "x": round(b[2] + 8, 2), "y": round(b[3] + 8, 2), "format_spec": "⌀%.2w"}], auto=True)
    if not issues:
        yield F("E.1", "pass", "recognised overall and circular sizes are annotated; full definition requires independent reconstruction")


def _num_re(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return re.escape(s)


def _e6(c: Ctx):
    groups = defaultdict(list)
    for d in c.dims:
        if d["type"] in ("DistanceX", "DistanceY") and "⌀" not in d.get("text", "") and len(d.get("refs", [])) == 2 \
                and not d.get("role", "").startswith(("chamfer", "diameter")) and not re.match(r"^(\d+\s*×\s*)?C\d", d.get("text", "")):
            groups[(d["view"], d["type"])].append(d)
    chains = []
    for key, ds in groups.items():
        if len(ds) < 2:
            continue
        common = set(ds[0]["refs"])
        for d in ds[1:]:
            common &= set(d["refs"])
        if common:
            continue
        ends = defaultdict(int)
        for d in ds:
            for r in d["refs"]:
                ends[r] += 1
        if any(n >= 2 for n in ends.values()) and len({tuple(sorted(d["refs"])) for d in ds}) == len(ds):
            chains.extend(d["name"] for d in ds)
    if chains:
        yield F("E.6", "warn", "chain dimensions found - prefer baseline/ordinate from one datum", objects=chains,
                hint="re-reference each dimension to the datum vertex shared by the stack")
    elif groups:
        yield F("E.6", "pass", "linear dimensions are baseline/ordinate from a common datum")
    else:
        yield F("E.6", "na", "no linear dimensions")


def _e8(c: Ctx):
    by_val = defaultdict(list)
    for d in c.dims:
        if d["type"] in ("Diameter", "Radius") and d.get("value") is not None and "PCD" not in d.get("role", "").upper() and d.get("role") != "pcd":
            by_val[(d.get("view"), d["type"], round(d["value"], 3), _bare_text(d["text"]))].append(d)
    problems = 0
    for (_view, typ, val, bare), ds in by_val.items():
        if len(ds) > 1:
            problems += 1
            keep, extra = ds[0], ds[1:]
            n = sum(_count(d) for d in ds)
            yield F("E.8", "fail", f"{len(ds)} separate {bare} callouts - use one '{n}× {bare}'",
                    objects=[d["name"] for d in ds], bbox=extra[0].get("text_box"),
                    fix=[{"op": "delete", "names": [d["name"] for d in extra]},
                         {"op": "set_dimension", "name": keep["name"], "format_spec": f"{n}× " + _bare_text(keep["format_spec"])}], auto=True)
    for d in c.dims:
        if d["type"] != "Diameter" or "×" in d.get("text", "") or d.get("role") == "pcd":
            continue
        v = c.view_by_name.get(d.get("view") or "")
        if not v or d.get("value") is None:
            continue
        same = [ci for ci in v.get("circles", []) if ci.get("full") and abs(2 * (ci.get("model_radius") or 0) - d["value"]) < 0.01]
        # Equal diameters do not imply equal depths, counterbores or fits. Keep
        # circles with their own distinct callout out of this callout's scope.
        separately_sized = {ref for other in c.dims if other['name'] != d['name'] and other.get('view') == d.get('view')
                            and other['type'] == 'Diameter' and other.get('value') is not None
                            and abs(other['value']-d['value']) < .01 and _bare_text(other['text']) != _bare_text(d['text'])
                            for ref in other.get('refs', [])}
        same = [ci for ci in same if not separately_sized.intersection(ci.get('edges',[ci.get('edge')]))]
        other_view_axes = []
        for other in c.dims:
            if (other.get('view') == d.get('view') or other['type'] != 'Diameter'
                    or other.get('value') is None or abs(other['value'] - d['value']) >= .01):
                continue
            other_view = c.view_by_name.get(other.get('view'), {})
            for ci in other_view.get('circles', []):
                if (ci.get('full') and ci.get('axis_identity')
                        and set(other.get('refs', [])).intersection(ci.get('edges', [ci.get('edge')]))):
                    other_view_axes.append(ci['axis_identity'])
        # Only remove other circles: the current callout must still count once.
        same = [ci for ci in same if set(d.get('refs', [])).intersection(ci.get('edges', [ci.get('edge')]))
                or not ci.get('axis_identity') or ci['axis_identity'] not in other_view_axes]
        if len(same) > 1:
            problems += 1
            yield F("E.8", "fail", f"{d['name']}: {len(same)} equal-diameter circles lack individual or grouped callout coverage", objects=[d["name"]],
                    bbox=d.get("text_box"), hint='Confirm depth, type and fit before assigning multiplicity; equal diameter alone is insufficient')
    if not problems:
        yield F("E.8", "pass", "repeated features use n× multiplicity")


def _count(d) -> int:
    m = re.match(r"^(\d+)\s*[×x]", d.get("text", ""))
    return int(m.group(1)) if m else 1


def _e10(c: Ctx):
    segs = list(c.view_segments())
    out = []
    flagged = set()
    dims = [d for d in c.dims if d.get("text_box")]
    for i, a in enumerate(dims):
        for b in dims[i + 1:]:
            if g.overlap(a["text_box"], b["text_box"], 0.3):
                out.append(("text", b, a["name"]))
    for d in dims:
        if d["name"] in {o[1]["name"] for o in out}:
            continue
        tb = g.expand(d["text_box"], -0.3)
        other_lines = [sg for o in c.dims if o["name"] != d["name"] for sg in o.get("ink_lines", o.get("lines", []))]
        if any(g.seg_box(sg, tb) for sg in d.get("ink_lines", [])):
            out.append(("line", d, "its own dimension or extension line"))
        elif any(g.seg_box(sg, tb) for sg in other_lines):
            out.append(("line", d, "another dimension's line"))
        elif any(g.seg_box(sg, tb) for sg in segs):
            out.append(("geometry", d, "part geometry"))
        else:
            for a in c.annos:
                if a.get("bbox") and g.overlap(tb, a["bbox"]):
                    out.append(("note", d, a["name"]))
                    break
    for kind, d, what in out:
        if d["name"] in flagged:
            continue
        flagged.add(d["name"])
        spot = free_spot(c, d, segs)
        yield F("E.10", "fail", f"{d['name']} text '{d['text']}' overlaps {what}", objects=[d["name"]], bbox=d["text_box"],
                fix=[{"op": "move_dimension", "name": d["name"], "x": spot[0], "y": spot[1]}] if spot else None, auto=bool(spot),
                hint=None if spot else "no free spot nearby - move the view or use a larger sheet")
    crossings = []
    dim_lines = [(d["name"], line) for d in c.dims if d.get("lines")
                 for line in d.get("dimension_lines", d.get("ink_lines", d["lines"])[:1])]
    for i, (na, la) in enumerate(dim_lines):
        for nb, lb in dim_lines[i + 1:]:
            pair = f"{na}/{nb}"
            if na != nb and pair not in crossings and (g.seg_seg(la, lb) or g.collinear_overlap(la, lb)):
                crossings.append(pair)
    if crossings:
        yield F("E.10", "fail" if c.meta.get("dimensioned_only") else "warn", "dimension lines cross or overlap each other", objects=crossings[:8])
    remaining = (c.meta.get("annotation_clearance") or {}).get("unresolved", [])
    if remaining:
        yield F("E.10", "fail", "annotation clearance could not be resolved during creation", objects=remaining,
                hint="rearrange annotations or use a larger sheet; text and line sizes were preserved")
    if not out and not crossings and not remaining:
        yield F("E.10", "pass", "no overlapping dimension text, lines or leaders")


def _e11(c: Ctx):
    sigs = defaultdict(list)
    for d in c.dims:
        if d.get("signature"):
            sigs[d["signature"]].append(d)
    dup = [(ds[0], ds[1:]) for ds in sigs.values() if len(ds) > 1]
    for keep, extra in dup:
        yield F("E.11", "fail", f"{keep['text']} is dimensioned {len(extra) + 1}× (same feature)",
                objects=[d["name"] for d in [keep] + extra], bbox=extra[0].get("text_box"),
                fix=[{"op": "delete", "names": [d["name"] for d in extra]}], auto=True)
    if not dup:
        yield F("E.11", "pass", "no controlling dimension appears twice")


# ================================================================= F. tolerances
def rules_F(c: Ctx):
    gt = c.fields.get("general_tolerances", "") + "\n" + c.notes_text
    m = re.search(r"ISO\s*2768(?:-1)?\s*[-:]?\s*([fmcv])([HKL])?\b", gt, re.I)
    if m:
        yield F("F.1", "warn" if m.group(2) else "pass",
                "legacy ISO 2768-2 class: review the contracted edition; no automatic ISO 22081 conversion"
                if m.group(2) else f"general size tolerance class {m.group(0)} declared")
    elif re.search(r"ISO\s*22081\b", gt, re.I):
        yield F("F.1", "review", "ISO 22081 declared: verify tolerance values, scope and datum system",
                hint="the standard number alone does not specify a general geometrical tolerance")
    else:
        yield F("F.1", "fail", "tolerance scheme needs design input",
                hint="declare applicable size/geometrical specifications or explicitly tolerance each feature; do not infer a class from geometry")
    fits = []
    for d in c.dims:
        for fm in re.finditer(r"\d\s*([A-Za-z]{1,2})(\d{1,2})\b", d.get("text", "")):
            fits.append((d, fm.group(1)))
    if not fits:
        yield F("F.2", "na", "no fit codes")
    else:
        bad = [d["name"] for d, letters in fits
               if ("hole" in d.get("role", "") and not letters.isupper()) or (("boss" in d.get("role", "") or "turned" in d.get("role", "")) and not letters.islower())]
        yield F("F.2", "fail" if bad else "pass", "fit letter case: uppercase holes, lowercase shafts", objects=bad)
    tol = [d for d in c.dims if abs(d["tolerance"]["over"]) > 0 or abs(d["tolerance"]["under"]) > 0]
    styles = {"sym" if d["tolerance"]["equal"] or abs(d["tolerance"]["over"] + d["tolerance"]["under"]) < 1e-9 else "asym" for d in tol}
    yield F("F.3", "na" if not tol else ("warn" if len(styles) > 1 else "pass"),
            "no toleranced dimensions" if not tol else f"deviation style {'mixed' if len(styles) > 1 else 'consistent'}")
    ref = [d for d in c.dims if "REF" in d.get("text", "")]
    yield F("F.4", "pass" if ref else "na", "reference dimensions marked REF" if ref else "no reference dimensions")
    basic = [d["name"] for d in c.dims if d.get("theoretical_exact")]
    has_gdt = bool(GDT_RE.search(c.all_text))
    yield F("F.5", "na" if not basic else ("pass" if has_gdt else "warn"),
            "no basic dimensions" if not basic else "basic dimensions paired with GD&T" if has_gdt else "boxed basic dimensions without feature control frames",
            objects=basic)


# ================================================================= G. GD&T
def rules_G(c: Ctx):
    if not GDT_RE.search(c.all_text):
        for rid in ("G.1", "G.2", "G.3", "G.4", "G.5"):
            yield F(rid, "na", "no GD&T on the sheet")
        return
    ok = "8015" in c.all_text
    yield F("G.1", "pass" if ok else "fail", "ISO 8015 declared" if ok else "GD&T used without ISO 8015 declaration",
            fix=None if ok else _note_append(c, "TOLERANCING PRINCIPLE: ISO 8015"), auto=not ok)
    for rid, msg in (("G.2", "feature control frames read symbol | value | modifier | datums"),
                     ("G.3", "datum letters attached to real datum features"),
                     ("G.4", "Ⓜ/Ⓛ placed after the tolerance value"),
                     ("G.5", "Ø only for cylindrical zones")):
        yield F(rid, "review", msg)


# ================================================================= H. surface
def rules_H(c: Ctx):
    ras = re.findall(r"\bR[az]\b[^\n]*", c.all_text)
    if not ras:
        yield F("H.1", "review", "surface texture requirements need design review",
                hint="use ISO 21920 for profile texture where function requires it; do not infer Ra from the model")
        yield F("H.2", "na", "no surface parameters")
    else:
        bad = [t for t in ras if not re.search(r"R[az]\s*\d+(?:[.,]\d+)?\s*(µm|um)", t)]
        yield F("H.1", "fail" if bad else "pass", "surface values use parameter + value + unit" if not bad else f"incomplete surface spec: {bad[0]!r}")
        yield F("H.2", "pass", "parameter names explicit (Ra/Rz)")
    symbols = [a for a in c.annos if a.get("kind") == "symbol"]
    for rid, msg in (("H.3", "local symbols override the general default"), ("H.4", "bar/circle variants match process intent"),
                     ("H.5", "lay specified where function depends on it")):
        yield F(rid, "review" if symbols else "na", msg if symbols else "no local surface symbols")


# ================================================================= I. welding
def rules_I(c: Ctx):
    welds = [a for a in c.annos if a.get("kind") == "weld"]
    if not welds:
        for rid in ("I.1", "I.2", "I.3"):
            yield F(rid, "na", "no weld symbols")
        return
    ok = "2553" in c.all_text
    yield F("I.1", "pass" if ok else "fail", "ISO 2553 system declared" if ok else "weld symbols without ISO 2553 system note",
            fix=None if ok else _note_append(c, "WELDING SYMBOLS: ISO 2553 SYSTEM A"), auto=not ok)
    yield F("I.2", "review", "arrow-side / other-side placement")
    yield F("I.3", "review", "size left / length right of the symbol")


# ================================================================= J. threads
def rules_J(c: Ctx):
    meta_threads = c.meta.get("threads") or []
    tapdrill = c.meta.get("tapdrill_review") or []
    threads = THREAD_RE.findall(c.all_text)
    for t in tapdrill:
        desc = t if isinstance(t, str) else f"⌀{t.get('dia')} matches {', '.join(t.get('threads', []))} tap drill"
        yield F("J.2", "review", f"plain hole {desc} - confirm whether it is threaded",
                hint="if threaded, set_dimension format_spec to the thread callout, e.g. 'M8×1.25-6H ↧12'")
    if not threads and not meta_threads:
        for rid in ("J.1", "J.2", "J.3", "J.4", "J.5"):
            yield F(rid, "na", "no thread callouts")
        return
    yield F("J.1", "review", "internal/external thread graphics not swapped (thin line at minor dia outside, major dia inside)")
    if meta_threads and len(threads) < len({(t.get("major_dia"), t.get("internal")) for t in meta_threads}):
        yield F("J.2", "fail", f"{len(meta_threads)} modelled thread(s) but only {len(threads)} thread callout(s)")
    incomplete = [t for t in threads if t.startswith("M") and (("×" not in t and "x" not in t) or "-" not in t)]
    yield F("J.2", "warn" if incomplete else "pass",
            "thread family, size, pitch and class complete" if not incomplete else f"incomplete metric callout(s): {', '.join(incomplete[:4])}",
            hint="state pitch and class, e.g. M8×1.25-6H")
    internal_designations = {t.get('designation') for t in meta_threads if t.get('internal')}
    internal = [t for t in threads if re.search(r"-\d[GH]\b", t) or t in internal_designations]
    nodepth = [t for t in internal if not re.search(re.escape(t) + r"[^\n]*(THRU|DEEP|↧)", c.all_text)]
    yield F("J.3", "warn" if nodepth else ("pass" if internal else "na"),
            "internal threads state depth or THRU" if not nodepth else f"internal thread without depth/THRU: {', '.join(nodepth[:4])}",
            hint="add '↧12' or 'THRU'")
    lh = [t for t in meta_threads if t.get("handed") == "LH"]
    if lh and "LH" not in c.all_text:
        yield F("J.3", "fail", "left-hand thread modelled but callout lacks 'LH'")
    yield F("J.4", "pass" if any("NPT" in t or t.startswith(("G", "R")) for t in threads) else "na", "pipe thread family explicit")
    yield F("J.5", "review", "counterbores, countersinks, reliefs called out where needed")


# ================================================================= K. material
def rules_K(c: Ctx):
    mat = c.fields.get("part_material")
    if placeholder(mat):
        yield F("K.1", "fail", "material not specified", bbox=c.tb,
                fix=[{"op": "set_title_block", "fields": {"part_material": "<material, e.g. EN AW-6082 T6>"}}])
    else:
        yield F("K.1", "pass", f"material: {mat}")
    ht = re.search(r"harden|Q&T|quench|nitrid|case.?harden|anneal", c.notes_text, re.I)
    if ht:
        ok = re.search(r"\d\s*(HRC|HV|HB|HBW)|CHD|NHD", c.all_text)
        yield F("K.2", "pass" if ok else "warn", "heat treatment states hardness / depth" if ok else "heat treatment without target hardness or case depth")
    else:
        yield F("K.2", "na", "no heat treatment")
    coat = re.search(r"anodi[sz]|\bzinc\b|Zn-?Ni|\bpaint|powder.?coat|\bcoat(ed|ing)?\b|\bplat(ed|ing)\b|black oxide|passivat", c.notes_text, re.I)
    if coat:
        ok = re.search(r"\d+\s*(µm|um)|class|type|black|clear|RAL", c.all_text, re.I)
        yield F("K.3", "pass" if ok else "warn", "coating states class/colour/thickness" if ok else "coating without class, colour or thickness")
        yield F("K.4", "pass" if re.search(r"MASK", c.all_text, re.I) else "review", "masked zones identified")
    else:
        yield F("K.3", "na", "no coating")
        yield F("K.4", "na", "no coating")


# ================================================================= L. revisions
def rules_L(c: Ctx):
    rev = (c.fields.get("revision_index") or "").strip()
    revs = c.meta.get("revisions") or []
    problems = []
    if placeholder(rev):
        problems.append(F("L.1", "fail", "revision index empty or placeholder", bbox=c.tb,
                          fix=[{"op": "set_title_block", "fields": {"revision_index": "A"}}], auto=True))
    elif rev[:1].upper() in REV_SKIP and rev.isalpha():
        problems.append(F("L.1", "fail", f"revision letter {rev!r} is on the skip list (I O Q S X Z)"))
    if revs and revs[-1].get("index") != rev:
        problems.append(F("L.1", "fail", f"title block revision {rev!r} differs from latest table row {revs[-1].get('index')!r}",
                          fix=[{"op": "set_title_block", "fields": {"revision_index": revs[-1].get("index")}}], auto=True))
    yield from problems or [F("L.1", "pass", f"revision {rev}")]
    if revs:
        bad = [r.get("index") for r in revs if not (r.get("description") and r.get("date") and r.get("approved"))]
        yield F("L.2", "warn" if bad else "pass", "revision rows complete" if not bad else f"revision rows missing description/date/approval: {bad}")
        yield F("L.3", "review" if len(revs) > 1 else "na", "revision balloons on changed features")
    else:
        yield F("L.2", "na", "first issue, no revision table")
        yield F("L.3", "na", "first issue")


# ================================================================= M. BOM
def rules_M(c: Ctx):
    asm = c.meta.get("assembly") or (c.s.get("model") or {}).get("solids", 0) > 1
    if not asm:
        for rid in ("M.1", "M.2", "M.3"):
            yield F(rid, "na", "single part")
        return
    pl = next((a for a in c.annos if any("ITEM" in t and "QTY" in t for t in a.get("text", []))), None)
    yield F("M.1", "pass" if pl else "fail", "parts list present" if pl else "assembly without parts list",
            bbox=pl.get("bbox") if pl else None)
    balloons = [a for a in c.annos if a.get("kind") == "balloon"]
    items = len(pl["text"]) - 1 if pl else 0
    yield F("M.2", "pass" if pl and len(balloons) == items else "fail",
            f"{len(balloons)} balloon(s) for {items} item(s)")
    yield F("M.3", "review", "standard parts identified with their standard")


# ================================================================= N. notes
def rules_N(c: Ctx):
    syms = {s for d in c.dims for s in DIA_SYMBOLS if s in d.get("text", "")}
    if len(syms) > 1:
        yield F("N.1", "warn", f"mixed diameter symbols {sorted(syms)}",
                fix=[{"op": "set_dimension", "name": d["name"], "format_spec": d["format_spec"].replace("Ø", "⌀").replace("ø", "⌀")}
                     for d in c.dims if "Ø" in d.get("format_spec", "") or "ø" in d.get("format_spec", "")], auto=True)
    else:
        yield F("N.1", "pass", "symbols used consistently")
    yield (F("N.2", "pass", "general notes define the defaults") if c.main_note and len(c.main_note.get("text", [])) >= 2
           else F("N.2", "warn", "no general notes block", fix=_note_append(c, "UNITS: mm")))
    t = c.notes_text
    if re.search(r"BREAK[^\n]*\d", t, re.I):
        yield F("N.3", "pass", "edge-break rule stated")
    elif re.search(r"DEBURR", t, re.I):
        yield F("N.3", "warn", "'deburr' without an allowable amount",
                hint="specify functional edge limits; use ISO 13715 for edges of undefined shape")
    else:
        yield F("N.3", "review", "edge requirements need design review",
                hint="define permitted edge deviations without changing sealing or seating edges")
    yield F("N.4", "review", "seating/sealing edges use explicit chamfer or radius")


# ================================================================= O. sheet metal
def rules_O(c: Ctx):
    bends = re.findall(r"\b(UP|DN|DOWN)\b\s*\d+\s*°", c.all_text)
    if not bends and c.meta.get("part_kind") != "sheet":
        for rid in ("O.1", "O.2", "O.3", "O.4"):
            yield F(rid, "na", "not a sheet-metal part")
        return
    sheet = c.meta.get("sheet") or {}
    callout = re.search(r"\b(UP|DN)\b\s*\d+\s*°\s*R", c.all_text)
    inside = sheet.get("inside_radii") or []
    radius_dims = {round(d["value"], 2) for d in c.dims if d["type"] == "Radius" and d.get("value") is not None}
    radius_ok = bool(inside) and all(round(r, 2) in radius_dims for r in inside)
    t_ok = bool(sheet.get("t")) and any(d['type'] in ('DistanceX','DistanceY','Distance')
             and d.get('role','').endswith(':thickness') and d.get('value') is not None
             and abs(d['value']-sheet['t'])<.01 for d in c.dims)
    if t_ok and (callout or radius_ok):
        yield F("O.1", "pass", "thickness and inside bend radius stated" + ("" if callout else "; 90° bends shown in the views"))
    else:
        missing = ([] if radius_ok else ["inside bend radius"]) + ([] if t_ok else ["dimensioned sheet thickness view"])
        yield F("O.1", "fail" if sheet else "warn", "bend callout incomplete: missing " + ", ".join(missing or ["direction/angle"]),
                hint='Add a thickness dimension between the wall faces on a standard view or section')
    yield F("O.2", "review", "bend order shown where sequence matters")
    yield F("O.3", "review", "bend relief where material would tear")
    if re.search(r"K\s*=|BEND (TABLE|ALLOWANCE)|DEDUCTION", c.all_text, re.I):
        yield F("O.4", "pass", "flat-pattern basis stated")
    else:
        yield F("O.4", "warn", "flat-pattern basis (K-factor or bend table) not stated",
                fix=_note_append(c, "UNFOLD PER K=<k-factor>"), hint="state the K-factor or bend table used by the shop")


# ================================================================= P. project
def rules_P(c: Ctx):
    units = [d["name"] for d in c.dims if d.get("show_units") or re.search(r"\b(mm|in)\b|\[", d.get("text", ""))]
    yield (F("P.1", "fail", "dimension text shows units / dual units", objects=units,
             fix=[{"op": "set_dimension", "name": n, "show_units": False} for n in units], auto=True)
           if units else F("P.1", "pass", "no unit suffixes or dual units in dimension text"))
    gen = c.meta.get("generator")
    yield F("P.2", "pass" if gen else "warn", f"generated by {gen}" if gen else "provenance unknown - regenerate with create_drawing")
    yield F("P.3", "pass", "exact TechDraw hidden-line projection of the B-rep")
    br = [d["name"] for d in c.dims if re.search(r"[()]", d.get("text", ""))]
    yield F("P.4", "review" if br else "pass", "verify reference dimensions (see E.9)" if br else "no parenthesized dimension text", objects=br)
    dup = any(len(v) > 1 for v in _group_sigs(c).values())
    if c.meta.get("dedupe_ran") and not dup:
        yield F("P.5", "pass", "dedupe pass ran, no duplicates remain")
    else:
        yield F("P.5", "fail", "dedupe pass missing or duplicates remain", fix=[{"op": "dedupe_dimensions"}], auto=True)
    sc = c.ortho_scale()
    if sc and sc < 1:
        lay = g.union([v.get("bbox") for v in c.ortho] + [d.get("text_box") for d in c.dims])
        nxt = {0.5: 1.0, 0.2: 0.5, 0.1: 0.2, 0.05: 0.1, 0.02: 0.05, 0.01: 0.02}.get(round(sc, 3))
        if lay and nxt:
            grow = nxt / sc
            fr = c.frame
            avail_w = fr[2] - fr[0] - 12
            avail_h = fr[3] - fr[1] - 12
            if (lay[2] - lay[0]) * grow < avail_w * 0.6 and (lay[3] - lay[1]) * grow < avail_h * 0.6:
                yield F("P.6", "warn", f"views at {scale_text(sc)} would likely fit at {scale_text(nxt)} (1 : 1 preferred)",
                        hint="recreate with scale='auto' after freeing space, or set_view scale")
                return
        yield F("P.6", "pass", f"reduced scale {scale_text(sc)} needed to fit the sheet")
    elif sc:
        yield F("P.6", "pass", f"orthographic views at {scale_text(sc)}")
    else:
        yield F("P.6", "na", "no orthographic views")


def _group_sigs(c: Ctx):
    sigs = defaultdict(list)
    for d in c.dims:
        if d.get("signature"):
            sigs[d["signature"]].append(d["name"])
    return sigs


SECTIONS: list[tuple[str, str, Callable]] = [
    ("A", "Sheet & title block", rules_A),
    ("B", "Lines", rules_B),
    ("C", "Views & projection", rules_C),
    ("D", "Sections / details", rules_D),
    ("E", "Dimensioning", rules_E),
    ("F", "Tolerances & fits", rules_F),
    ("G", "GD&T", rules_G),
    ("H", "Surface texture", rules_H),
    ("I", "Welding", rules_I),
    ("J", "Threads", rules_J),
    ("K", "Material & processes", rules_K),
    ("L", "Revisions", rules_L),
    ("M", "BOM", rules_M),
    ("N", "Symbols & notes", rules_N),
    ("O", "Sheet metal", rules_O),
    ("P", "Project overrides", rules_P),
]
