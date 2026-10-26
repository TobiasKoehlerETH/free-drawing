"""Dimension demand shared by scale selection and annotation placement."""


def balanced_stacks(specs):
    """Split crowded parallel stacks across opposite sides without dropping dimensions.

    Baseline origins and geometric references stay intact. View spacing is then
    calculated from these same stacks, including stacks between adjacent views.
    """
    result = [dict(spec) for spec in specs]
    groups = {}
    for spec in result:
        if spec.get("side") not in ("top", "bottom", "left", "right"):
            continue
        axis = "horizontal" if spec["side"] in ("top", "bottom") else "vertical"
        groups.setdefault((spec["view"].name, axis), []).append(spec)
    opposite = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
    for items in groups.values():
        if len(items) < 4 or len({s["side"] for s in items}) > 1:
            continue
        items.sort(key=lambda s: abs(s["span"][1] - s["span"][0]))
        side = items[0]["side"]
        for index, spec in enumerate(items):
            spec["side"] = side if index % 2 else opposite[side]
    return result


def preserves_coverage(original, revised):
    """A replacement plan must still represent every previously placed requirement."""
    return {s["requirement_id"] for s in original} <= {s["requirement_id"] for s in revised}


def retain_parent_dimensions(original, revised):
    """Keep a useful existing dimension on a section/detail parent when possible.

    Move the requirement back; never duplicate it or move a visible dimension
    onto hidden geometry. Keep at least one dimension on its current view.
    """
    result = list(revised)
    originals = {s["requirement_id"]: s for s in original}
    parents = {view.BaseView.Name for s in result
               if (view := s["view"].view) is not None and getattr(view, "BaseView", None) is not None}
    for parent in sorted(parents):
        counts = {}
        for spec in result:
            name = spec["view"].view.Name
            counts[name] = counts.get(name, 0) + 1
        if counts.get(parent):
            continue
        candidates = []
        for index, spec in enumerate(result):
            previous = originals.get(spec["requirement_id"])
            if (previous is not None and previous["view"].view.Name == parent
                    and not previous.get("hidden") and counts[spec["view"].view.Name] > 1):
                span = previous.get("span", (0, 0))
                candidates.append((abs(span[1] - span[0]), index, previous))
        if candidates:
            _, index, previous = max(candidates, key=lambda row: (row[0], -row[1]))
            result[index] = dict(previous)
    return result


def pack_blocks(blocks, frame, keepouts, beam_width=24, footprints=None):
    """Place measured view/annotation rectangles with a bounded corner search.

    Returns lower-left positions by block name, or None. No rotation, scaling,
    or clipping is allowed here; callers choose one shared model scale first.
    """
    states = [({}, list(keepouts))]
    for name, width, height in sorted(blocks, key=lambda b: -(b[1] * b[2])):
        pieces = (footprints or {}).get(name, [[0, 0, width, height]])
        next_states = []
        for positions, occupied in states:
            xs = {frame[0], frame[2] - width}
            ys = {frame[1], frame[3] - height}
            for box in occupied:
                for piece in pieces:
                    xs.update((box[2] - piece[0], box[0] - piece[2]))
                    ys.update((box[3] - piece[1], box[1] - piece[3]))
            for x in sorted(xs):
                for y in sorted(ys, reverse=True):
                    box = [x, y, x + width, y + height]
                    if box[0] < frame[0] or box[1] < frame[1] or box[2] > frame[2] or box[3] > frame[3]:
                        continue
                    placed = [[x + p[0], y + p[1], x + p[2], y + p[3]] for p in pieces]
                    if any(p[0] < b[2] and b[0] < p[2] and p[1] < b[3] and b[1] < p[3] for b in occupied for p in placed):
                        continue
                    new_positions = dict(positions, **{name: (x, y)})
                    # Keep diverse corner placements; favour compact top-left layouts.
                    cost = sum(px + frame[3] - py for px, py in new_positions.values())
                    next_states.append((cost, new_positions, occupied + placed))
        if not next_states:
            return None
        next_states.sort(key=lambda s: s[0])
        states = [(p, o) for _, p, o in next_states[:beam_width]]
    return states[0][0]
