"""Sheet-metal awareness for dimensioning [guidelines/15].

A bent sheet part has one wall thickness ``t`` everywhere: every planar wall
face has a parallel partner ``t`` away with the opposite normal, and every
bend is a pair of coaxial partial cylinders with radii ``R`` and ``R + t``.

Drawing convention:

* thickness dimensioned once between two visible wall faces;
* face positions measured to the OUTSIDE faces only - inner faces follow
  from the wall-thickness dimension;
* bends called out by their INSIDE radius (``4× R2``), never the outer one.
"""

import math
from collections import defaultdict

import FreeCAD as App

from .analysis import cylinders
from .geometry import exact_bbox


def _planes(shape):
    out = []
    for f in shape.Faces:
        if type(f.Surface).__name__ != "Plane":
            continue
        try:
            u0, u1, v0, v1 = f.ParameterRange
            n = f.normalAt(0.5 * (u0 + u1), 0.5 * (v0 + v1))
        except Exception:
            continue
        out.append({"face": f, "n": App.Vector(n), "c": f.CenterOfMass, "area": f.Area,
                    "d": App.Vector(n).dot(f.CenterOfMass)})
    return out


def detect(shape, max_ratio=0.12):
    """``{"t", "inner_radii", "bends"}`` for a constant-thickness bent part, else None."""
    planes = _planes(shape)
    if len(planes) < 4:
        return None
    bb = exact_bbox(shape)
    big = max(bb.XLength, bb.YLength, bb.ZLength)
    votes = defaultdict(float)
    total = sum(p["area"] for p in planes)
    for i, a in enumerate(planes):
        for b in planes[i + 1:]:
            if a["n"].dot(b["n"]) > -0.9999:
                continue  # need opposite normals
            gap = (b["c"] - a["c"]).dot(a["n"])
            if gap >= -1e-6:
                continue  # b must lie behind a (material in between)
            t = round(-gap, 3)
            if t <= 0 or t > max_ratio * big:
                continue
            votes[t] += min(a["area"], b["area"])
    if not votes:
        return None
    t, w = max(votes.items(), key=lambda kv: kv[1])
    if 2 * w < 0.6 * total:
        return None
    radii = defaultdict(set)
    for c in cylinders(shape):
        if c["full"] or not c["axis"]:
            continue
        i = "XYZ".index(c["axis"])
        key = (c["axis"],) + tuple(round(c["center"][k], 2) for k in range(3) if k != i)
        radii[key].add(round(c["radius"], 3))
    bends = []
    for key, rs in radii.items():
        for r in sorted(rs):
            if round(r + t, 3) in rs:
                bends.append({"axis": key[0], "inner": r, "outer": round(r + t, 3)})
    if not bends:
        return None  # a plate with holes is a plate, not a bent sheet part
    return {"t": t, "bends": bends, "inner_radii": sorted({b["inner"] for b in bends})}


def inner_positions(shape, t):
    """Planar positions ``{axis: {coord}}`` that are inner faces of a t-thick wall."""
    planes = _planes(shape)
    bb = exact_bbox(shape)
    center = bb.Center
    inner = defaultdict(set)
    outer = defaultdict(set)
    for a in planes:
        ax = None
        for name, vec in (("X", App.Vector(1, 0, 0)), ("Y", App.Vector(0, 1, 0)), ("Z", App.Vector(0, 0, 1))):
            if abs(abs(a["n"].dot(vec)) - 1) < 1e-6:
                ax = name
        if not ax:
            continue
        coord = round(getattr(a["c"], ax.lower()), 3)
        partner = any(b is not a and b["n"].dot(a["n"]) < -0.9999 and abs(abs((b["c"] - a["c"]).dot(a["n"])) - t) < 1e-3
                      for b in planes)
        if not partner:
            outer[ax].add(coord)
            continue
        facing_out = a["n"].dot(a["c"] - center) > 0
        (outer if facing_out else inner)[ax].add(coord)
    return {ax: inner[ax] - outer[ax] for ax in "XYZ"}


def filter_requirements(reqs, shape, sheet):
    """Drop inner-face positions and outer bend radii; return (reqs, removed)."""
    t = sheet["t"]
    inner = inner_positions(shape, t)
    outer_r = {b["outer"] for b in sheet["bends"]}
    keep, removed = [], []
    for r in reqs:
        if r["kind"] == "loc" and r.get("source") == "face" and round(r["coord"], 3) in inner.get(r["axis"], set()):
            removed.append(r)
            continue
        if r["kind"] == "radius" and round(r["radius"], 3) in outer_r and round(r["radius"] - t, 3) in sheet["inner_radii"]:
            removed.append(r)
            continue
        keep.append(r)
    planes = _planes(shape)
    pairs = []
    for a in planes:
        axis = next((name for index, name in enumerate('XYZ') if abs(a['n'][index]) > .9999), None)
        if axis is None:
            continue
        for b in planes:
            if a['n'].dot(b['n']) > -.9999:
                continue
            gap = (b['c'] - a['c']).dot(a['n'])
            if abs(gap + t) < 1e-3:
                values = sorted((getattr(a['c'], axis.lower()), getattr(b['c'], axis.lower())))
                pairs.append((min(a['area'], b['area']), axis, values))
    if pairs:
        _, axis, (datum, coordinate) = max(pairs)
        keep.append({'kind': 'loc', 'axis': axis, 'datum': datum, 'coord': coordinate,
                     'source': 'thickness', 'center': False, 'prio': 4})
    return keep, removed
