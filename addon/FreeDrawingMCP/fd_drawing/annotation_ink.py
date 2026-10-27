"""Paper-space strokes shared by annotation placement and export inspection."""
import math


def angular_ink(record):
    """Return the measured minor arc and its witness extensions in page mm."""
    geometry = record.get("angle_geometry")
    if not geometry:
        return [], []
    vertex, ends = geometry["vertex"], geometry["ends"]
    start, finish = [math.atan2(p[1] - vertex[1], p[0] - vertex[0]) for p in ends]
    sweep = (finish - start + math.pi) % (2 * math.pi) - math.pi
    center, box = record["text_center"], record["text_box"]
    middle = start + sweep / 2
    opposite = ((center[0] - vertex[0]) * math.cos(middle)
                + (center[1] - vertex[1]) * math.sin(middle)) < 0
    if opposite:
        start += math.pi
    clearance = math.hypot(box[2] - box[0], box[3] - box[1]) / 2 + 1
    radius = max(4, math.dist(center, vertex) - clearance)
    count = max(8, math.ceil(abs(sweep) * radius / 1.5))
    points = [[vertex[0] + radius * math.cos(start + sweep * i / count),
               vertex[1] + radius * math.sin(start + sweep * i / count)] for i in range(count + 1)]
    extensions = []
    for end, angle in zip(ends, (start, start + sweep)):
        distance = math.dist(end, vertex) * (-1 if opposite else 1)
        if abs(radius - distance) <= 1.2:
            continue
        sign = 1 if radius > distance else -1
        a, b = distance + sign, radius + sign * 2
        extensions.append([vertex[0] + a * math.cos(angle), vertex[1] + a * math.sin(angle),
                           vertex[0] + b * math.cos(angle), vertex[1] + b * math.sin(angle)])
    return points, extensions


def dimension_ink(record):
    lines = record.get("lines", [])
    box, center = record.get("text_box"), record.get("text_center")
    kind = record.get("type")
    if kind == "Angle" and record.get("angle_geometry"):
        points, extensions = angular_ink(record)
        return [a + b for a, b in zip(points, points[1:])] + extensions
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


def dimension_lines(record):
    """The dimension arc/leader itself, excluding witness extension lines."""
    if record.get("angle_geometry"):
        points, _ = angular_ink(record)
        return [a + b for a, b in zip(points, points[1:])]
    ink = record.get("ink_lines", record.get("lines", []))
    return ink if record.get("type") in ("Diameter", "Radius") else ink[:1]


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
    result["dimension_lines"] = dimension_lines(result)
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
        if 1e-5 < t < 1 - 1e-5 and 1e-5 < s < 1 - 1e-5:
            return True
    length = math.hypot(*u)
    if length < tolerance or any(abs(cross(x - ax, y - ay, *u)) / length > tolerance
                                 for x, y in ((cx, cy), (dx, dy))):
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
