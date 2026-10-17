"""3D shape analysis feeding view selection and dimensioning."""

import math

import FreeCAD as App

from . import geometry as G

_AXES = {"X": App.Vector(1, 0, 0), "Y": App.Vector(0, 1, 0), "Z": App.Vector(0, 0, 1)}


def _axis_of(vec, tol=1e-4):
    v = App.Vector(vec)
    if v.Length < 1e-12:
        return None
    v.normalize()
    for name, a in _AXES.items():
        if abs(abs(v.dot(a)) - 1.0) < tol:
            return name
    return None


def cylinders(shape):
    """Cylindrical features grouped by radius/axis.

    Each entry: ``{"radius", "axis" ('X'|'Y'|'Z'|None), "center" (3D point on
    axis), "internal" (hole), "through", "extent" (min,max along axis),
    "full" (closed 360 deg surface)}``.
    """
    out = []
    bb = G.exact_bbox(shape)
    for face in shape.Faces:
        surf = face.Surface
        if type(surf).__name__ != "Cylinder":
            continue
        axis_vec = App.Vector(surf.Axis)
        axis = _axis_of(axis_vec)
        u0, u1, v0, v1 = face.ParameterRange
        full = (u1 - u0) > 2 * math.pi - 1e-3
        um, vm = 0.5 * (u0 + u1), 0.5 * (v0 + v1)
        p = face.valueAt(um, vm)
        n = face.normalAt(um, vm)
        c = App.Vector(surf.Center)
        a = App.Vector(axis_vec); a.normalize()
        foot = c + a * (p - c).dot(a)
        radial = p - foot
        internal = n.dot(radial) < 0
        ts = [(App.Vector(vx.Point) - c).dot(a) for vx in face.Vertexes] or [0.0]
        fb = face.BoundBox
        if axis:
            lo = getattr(fb, axis + "Min"); hi = getattr(fb, axis + "Max")
            slo = getattr(bb, axis + "Min"); shi = getattr(bb, axis + "Max")
            through = internal and lo <= slo + 1e-3 and hi >= shi - 1e-3
            extent = (lo, hi)
        else:
            through = False
            extent = (min(ts), max(ts))
        out.append({
            "radius": float(surf.Radius),
            "axis": axis,
            "center": (float(foot.x), float(foot.y), float(foot.z)),
            "internal": bool(internal),
            "through": bool(through),
            "extent": (float(extent[0]), float(extent[1])),
            "full": bool(full),
            "span": float(min(u1 - u0, 2 * math.pi)),
        })
    # Merge split faces of the same cylinder (OCC often splits 360 deg seams).
    merged = {}
    for cyl in out:
        ax = cyl["axis"] or "?"
        c = cyl["center"]
        idx = {"X": (1, 2), "Y": (0, 2), "Z": (0, 1)}.get(ax, (0, 1))
        key = (ax, round(cyl["radius"], 3), round(c[idx[0]], 2), round(c[idx[1]], 2), cyl["internal"])
        if key in merged:
            m = merged[key]
            m["span"] = m.get("span", 0.0) + cyl.get("span", 0.0)
            m["full"] = m["span"] >= 2 * math.pi - 1e-3
            m["extent"] = (min(m["extent"][0], cyl["extent"][0]), max(m["extent"][1], cyl["extent"][1]))
            m["through"] = m["through"] or cyl["through"]
        else:
            merged[key] = dict(cyl)
    return list(merged.values())


def classify(shape):
    """Return ``{"kind": 'plate'|'turned'|'prismatic', ...}`` with extents."""
    bb = G.exact_bbox(shape)
    ext = {"X": bb.XLength, "Y": bb.YLength, "Z": bb.ZLength}
    order = sorted(ext, key=lambda k: ext[k])
    thin, mid, big = order
    cyls = cylinders(shape)
    planar_only = all(type(f.Surface).__name__ in ("Plane", "Cylinder") for f in shape.Faces)
    info = {"extents": ext, "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax], "cylinders": cyls}
    # Plate: one small extent, features go through it.
    if ext[thin] > 0 and ext[thin] <= 0.12 * ext[mid] and planar_only:
        holes_ok = all((not c["internal"]) or c["axis"] == thin for c in cyls)
        if holes_ok:
            info.update(kind="plate", thickness=ext[thin], thin_axis=thin)
            return info
    # Turned: all curved faces share one axis and an outer cylinder bounds the part.
    outer = [c for c in cyls if not c["internal"] and c["full"]]
    if outer:
        ax = outer[0]["axis"]
        coaxial = ax and all(
            c["axis"] == ax and _same_line(c, outer[0]) for c in cyls
        )
        others = [k for k in "XYZ" if k != ax]
        round_section = ax and abs(ext[others[0]] - ext[others[1]]) < 1e-3
        if coaxial and round_section:
            info.update(kind="turned", axis=ax)
            return info
    info.update(kind="prismatic")
    return info


def _same_line(a, b, tol=1e-3):
    ax = a["axis"]
    if ax != b["axis"] or ax is None:
        return False
    idx = {"X": (1, 2), "Y": (0, 2), "Z": (0, 1)}[ax]
    return all(abs(a["center"][i] - b["center"][i]) < tol for i in idx)


def hole_groups(shape):
    """Identical holes grouped for multiplicity callouts (``4× ⌀6 THRU``)."""
    groups = {}
    for c in cylinders(shape):
        if not c["internal"] or not c["full"]:
            continue
        key = (c["axis"], round(2 * c["radius"], 3), c["through"])
        groups.setdefault(key, []).append(c)
    out = []
    for (axis, dia, through), items in sorted(groups.items(), key=lambda kv: -kv[0][1]):
        out.append({"axis": axis, "diameter": dia, "through": through, "count": len(items), "items": items})
    return out
