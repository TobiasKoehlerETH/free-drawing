"""2D helpers for page-space checks (mm, Y up). Boxes are [x0, y0, x1, y1]."""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Box = Sequence[float]


def overlap(a: Box | None, b: Box | None, pad: float = 0.0) -> bool:
    if not a or not b:
        return False
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter_area(a: Box, b: Box) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def expand(b: Box, d: float) -> list[float]:
    return [b[0] - d, b[1] - d, b[2] + d, b[3] + d]


def union(boxes: Iterable[Box | None]) -> list[float] | None:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def inside(inner: Box, outer: Box, tol: float = 0.01) -> bool:
    return inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol


def translate(b: Box, dx: float, dy: float) -> list[float]:
    return [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]


def seg_box(seg: Sequence[float], box: Box) -> bool:
    """Segment (x1, y1, x2, y2) touches box."""
    x1, y1, x2, y2 = seg
    bx0, by0, bx1, by1 = box
    if max(x1, x2) < bx0 or min(x1, x2) > bx1 or max(y1, y2) < by0 or min(y1, y2) > by1:
        return False
    if bx0 <= x1 <= bx1 and by0 <= y1 <= by1:
        return True
    if bx0 <= x2 <= bx1 and by0 <= y2 <= by1:
        return True
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx0), (dx, bx1 - x1), (-dy, y1 - by0), (dy, by1 - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def seg_seg(a: Sequence[float], b: Sequence[float], eps: float = 1e-6) -> bool:
    """Proper crossing of two segments (touching ends do not count)."""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    p1, p2 = (a[0], a[1]), (a[2], a[3])
    q1, q2 = (b[0], b[1]), (b[2], b[3])
    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 * d2 < -eps) and (d3 * d4 < -eps)


def collinear_overlap(a: Sequence[float], b: Sequence[float], tolerance: float = 0.2) -> bool:
    dx, dy = a[2] - a[0], a[3] - a[1]
    length = math.hypot(dx, dy)
    if length < tolerance:
        return False
    if any(abs((x - a[0]) * dy - (y - a[1]) * dx) / length > tolerance for x, y in (b[:2], b[2:])):
        return False
    lo, hi = sorted(((x - a[0]) * dx / length + (y - a[1]) * dy / length for x, y in (b[:2], b[2:])))
    return min(length, hi) - max(0, lo) > tolerance


def polyline_segments(flat: Sequence[float]):
    for i in range(0, len(flat) - 3, 2):
        yield (flat[i], flat[i + 1], flat[i + 2], flat[i + 3])


def length(seg: Sequence[float]) -> float:
    return math.hypot(seg[2] - seg[0], seg[3] - seg[1])
