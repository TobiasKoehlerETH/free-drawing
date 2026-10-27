"""Move dimension labels without changing references, values, or paper text size."""
import math

from . import geometry as G
from .annotation_ink import angular_ink, dimension_lines, shifted_dimension, strokes_conflict
from .snapshot import _dimension_record, _view_record


def clear_collisions(page, dims, views, max_passes=4, bounds=None, keepouts=()):
    records = {d.Name: _dimension_record(d) for d in dims}
    geometry, fixed_text = drawing_obstacles(views)
    moves = 0
    for _ in range(max_passes):
        changed = False
        for dim in dims:
            record = records[dim.Name]
            if not record.get("text_box") or record["type"] not in ("DistanceX", "DistanceY", "Diameter", "Radius", "Angle"):
                continue
            others = [r for name, r in records.items() if name != dim.Name]
            before = collision_cost(record, others, geometry, fixed_text, bounds, keepouts)
            if before == 0:
                continue
            best = (before, 0, 0, record)
            for dx, dy in offsets(record):
                candidate = shifted_dimension(record, dx, dy)
                cost = collision_cost(candidate, others, geometry, fixed_text, bounds, keepouts)
                if cost < best[0]:
                    best = (cost, dx, dy, candidate)
                    if not cost:
                        break
            if best[0] and record["type"] in ("DistanceX", "DistanceY"):
                for dx, dy in offsets(record, spacing=4):
                    candidate = shifted_dimension(record, dx, dy)
                    cost = collision_cost(candidate, others, geometry, fixed_text, bounds, keepouts)
                    if cost < best[0]:
                        best = (cost, dx, dy, candidate)
                        if not cost:
                            break
            cost, dx, dy, candidate = best
            if cost < before:
                dim.X = float(dim.X) + dx
                dim.Y = float(dim.Y) + dy
                records[dim.Name] = candidate
                moves += 1
                changed = True
        if not changed:
            break
    page.Document.recompute()
    unresolved = [name for name, r in records.items() if r.get("text_box") and
                  collision_cost(r, [other for key, other in records.items() if key != name], geometry, fixed_text, bounds, keepouts)]
    return {"moves": moves, "unresolved": unresolved}


def offsets(record, spacing=8):
    box = record["text_box"]
    candidates = []
    if record["type"] == "Angle" and record.get("angle_geometry"):
        vertex = record["angle_geometry"]["vertex"]
        start, finish = [math.atan2(p[1] - vertex[1], p[0] - vertex[0]) for p in record["angle_geometry"]["ends"]]
        sweep = (finish - start + math.pi) % (2 * math.pi) - math.pi
        direction = start + sweep / 2
        x, y = record["text_center"]
        clearance = math.hypot(box[2] - box[0], box[3] - box[1]) / 2 + 1
        for radius in range(6, 81, 4):
            for side in (1, -1):
                candidates.append((vertex[0] + side * (radius + clearance) * math.cos(direction) - x,
                                   vertex[1] + side * (radius + clearance) * math.sin(direction) - y))
    elif record["type"] in ("DistanceX", "DistanceY"):
        horizontal = record["type"] == "DistanceX"
        along = max(6, (box[2] - box[0] if horizontal else box[3] - box[1]) + 3)
        for level in range(-8, 9):
            for side in (0, -1, 1, -2, 2):
                delta = (side * along, level * spacing) if horizontal else (level * spacing, side * along)
                if delta != (0, 0):
                    candidates.append(delta)
    else:
        for radius in (6, 12, 20, 32, 48, 64):
            candidates.extend((radius * math.cos(math.radians(angle)), radius * math.sin(math.radians(angle)))
                              for angle in range(0, 360, 30))
    return sorted(candidates, key=lambda p: math.hypot(*p))


def collision_cost(record, others, geometry, fixed_text, bounds=None, keepouts=()):
    box = G.expand(record["text_box"], 0.6)
    ink = record.get("ink_lines", record.get("lines", []))
    text_hits = sum(G.seg_hits_box(line, box) for line in geometry)
    if record.get("angle_geometry"):
        arc, _ = angular_ink(record)
        vertex = record["angle_geometry"]["vertex"]
        middle = arc[len(arc) // 2]
        center = record["text_center"]
        a = math.atan2(middle[1] - vertex[1], middle[0] - vertex[0])
        b = math.atan2(center[1] - vertex[1], center[0] - vertex[0])
        # A free-floating angle number must be placed beside its own arc.
        text_hits += int(math.dist(center, vertex) < 5 or abs((b - a + math.pi) % (2 * math.pi) - math.pi) > .04)
    text_hits += sum(G.seg_hits_box(line, G.expand(record["text_box"], -0.15)) for line in ink)
    text_hits += sum(G.boxes_overlap(box, b) for b in fixed_text)
    text_hits += sum(G.boxes_overlap(box, b) for b in keepouts if b)
    text_hits += sum(G.seg_hits_box(line, b) for line in ink for b in keepouts if b)
    if bounds and (box[0] < bounds[0] or box[1] < bounds[1] or box[2] > bounds[2] or box[3] > bounds[3]):
        text_hits += 10
    if bounds:
        text_hits += sum(not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])
                         for line in ink for x, y in (line[:2], line[2:]))
    crossings = 0
    for other in others:
        other_box = other.get("text_box")
        other_ink = other.get("ink_lines", other.get("lines", []))
        if other_box:
            text_hits += int(G.boxes_overlap(box, other_box))
            text_hits += sum(G.seg_hits_box(line, G.expand(other_box, 0.6)) for line in ink)
        text_hits += sum(G.seg_hits_box(line, box) for line in other_ink)
        if ink and other_ink:
            crossings += int(any(strokes_conflict(a, b) for a in dimension_lines(record) for b in dimension_lines(other)))
    return 100 * text_hits + crossings


def drawing_obstacles(views):
    segments, labels = [], []
    for view in views:
        record = _view_record(view, True)
        for poly in record.get("polylines_visible", []) + record.get("polylines_hidden", []):
            segments.extend(poly[i:i + 4] for i in range(0, len(poly) - 3, 2))
        for cosmetic in record.get("cosmetic", []):
            poly = cosmetic.get("points", [])
            segments.extend(poly[i:i + 4] for i in range(0, len(poly) - 3, 2))
        for key in ("boundary", "detail_circle"):
            circle = record.get(key)
            if not circle:
                continue
            x, y = circle["center"]
            radius = circle["radius"]
            points = [(x + radius * math.cos(i * math.pi / 32), y + radius * math.sin(i * math.pi / 32)) for i in range(65)]
            segments.extend([*a, *b] for a, b in zip(points, points[1:]))
            if circle.get("letter_at"):
                x, y = circle["letter_at"]
                labels.append([x - 2, y - 3, x + 2, y + 3])
        cutting = record.get("cutting_line") or {}
        points = cutting.get("points", [])
        segments.extend([*a, *b] for a, b in zip(points, points[1:]))
        for letter in cutting.get("letters", []):
            x, y = letter["at"]
            labels.append([x - 2, y - 3, x + 2, y + 3])
    return segments, labels
