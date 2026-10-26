"""Paper-space strokes shared by annotation placement and export inspection."""
import math


def dimension_ink(record):
    lines = record.get("lines", [])
    box, center = record.get("text_box"), record.get("text_center")
    kind = record.get("type")
    if kind in ("DistanceX", "DistanceY") and len(lines) >= 3 and center:
        k = 0 if kind == "DistanceX" else 1
        other = 1 - k
        p1, q1 = list(lines[1][:2]), list(lines[1][2:])
        p2, q2 = list(lines[2][:2]), list(lines[2][2:])
        level = q1[other]
        lo, hi = sorted((q1[k], q2[k]))
        if hi - lo < 7:
            lo, hi = lo - 5, hi + 5
        if box:
            lo, hi = min(lo, box[k] - 0.5), max(hi, box[k + 2] + 0.5)
        first, last = [0, 0], [0, 0]
        first[k], last[k] = lo, hi
        first[other] = last[other] = level
        ink = [first + last]
        for p, q in ((p1, q1), (p2, q2)):
            if abs(level - p[other]) > 1.2:
                direction = 1 if level >= p[other] else -1
                p[other] += direction
                q[other] = level + direction * 2
                ink.append(p + q)
        return ink
    if kind in ("Diameter", "Radius") and record.get("arc") and center and box:
        cx, cy = record["arc"]["center"]
        radius = record["arc"]["radius"]
        right = center[0] >= cx
        y = box[1] - 0.4
        elbow = [box[0] - 0.8 if right else box[2] + 0.8, y]
        end = [box[2] + 0.5 if right else box[0] - 0.5, y]
        angle = math.atan2(y - cy, elbow[0] - cx)
        tip = [cx + radius * math.cos(angle), cy + radius * math.sin(angle)]
        if record["arc"].get("points"):
            tip = min(record["arc"]["points"], key=lambda p: (p[0] - elbow[0]) ** 2 + (p[1] - elbow[1]) ** 2)
        return [tip + elbow, elbow + end]
    return lines


def shifted_dimension(record, dx, dy):
    result = dict(record)
    x, y = record["text_center"]
    result["text_center"] = [x + dx, y + dy]
    box = record["text_box"]
    result["text_box"] = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
    lines = [list(line) for line in record.get("lines", [])]
    if record["type"] == "DistanceX" and len(lines) >= 3:
        lines[0][1] += dy
        lines[0][3] += dy
        lines[1][3] += dy
        lines[2][3] += dy
    elif record["type"] == "DistanceY" and len(lines) >= 3:
        lines[0][0] += dx
        lines[0][2] += dx
        lines[1][2] += dx
        lines[2][2] += dx
    result["lines"] = lines
    result["ink_lines"] = dimension_ink(result)
    return result


def strokes_conflict(a, b, tolerance=0.2):
    """Crossing or overlapping strokes, excluding a shared endpoint."""
    ax, ay, bx, by = a
    cx, cy, dx, dy = b
    cross = lambda x, y, u, v: x * v - y * u
    u, v = (bx - ax, by - ay), (dx - cx, dy - cy)
    determinant = cross(*u, *v)
    if abs(determinant) > 1e-8:
        t = cross(cx - ax, cy - ay, *v) / determinant
        s = cross(cx - ax, cy - ay, *u) / determinant
        return 1e-5 < t < 1 - 1e-5 and 1e-5 < s < 1 - 1e-5
    length = math.hypot(*u)
    if length < tolerance or abs(cross(cx - ax, cy - ay, *u)) / length > tolerance:
        return False
    projections = sorted(((cx - ax) * u[0] / length + (cy - ay) * u[1] / length,
                          (dx - ax) * u[0] / length + (dy - ay) * u[1] / length))
    return min(length, projections[1]) - max(0, projections[0]) > tolerance


def caption_geometry(view, dims):
    box = view.get("bbox")
    if not box or not view.get("caption"):
        return None
    x, bottom = (box[0] + box[2]) / 2, box[1]
    if view.get("boundary"):
        bottom = min(bottom, view["boundary"]["center"][1] - view["boundary"]["radius"])
    for dim in dims:
        if dim.get("view") == view["name"]:
            if dim.get("text_box"):
                bottom = min(bottom, dim["text_box"][1])
            for line in dim.get("ink_lines", dim.get("lines", [])):
                bottom = min(bottom, line[1], line[3])
    y = bottom - 8
    half_width = 0.62 * 5.0 * len(view["caption"]) / 2
    return [x, y], [x - half_width, y - 1.5, x + half_width, y + 5]
