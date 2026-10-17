"""Robust CAD import for any STEP (AP203/AP214/AP242), IGES or BREP file.

Handles what real-world files throw at a drawing generator:

* not a STEP file / truncated / empty      -> clear ValueError
* invalid B-rep                            -> ``shape.fix`` and a warning
* surface-only models (closed shells)      -> sewn into solids when closed
* inch or other units                      -> OCC converts to mm on read
* parts placed at arbitrary rotation       -> aligned to the part's own frame
  (largest planar faces / cylinder axis / inertia) so views are true views
* assemblies (several solids)              -> component list with names from
  the STEP product structure, identical parts grouped for the parts list
"""

import math
import os

import FreeCAD as App
import Part

STEP_EXT = (".step", ".stp", ".p21")
IGES_EXT = (".iges", ".igs")
BREP_EXT = (".brep", ".brp")
SUPPORTED = STEP_EXT + IGES_EXT + BREP_EXT


def _check_file(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type {ext!r}; supported: {', '.join(SUPPORTED)}")
    size = os.path.getsize(path)
    if size == 0:
        raise ValueError(f"{os.path.basename(path)} is empty")
    if ext in STEP_EXT:
        with open(path, "rb") as f:
            head = f.read(4096).decode("latin-1", errors="replace")
        if "ISO-10303-21" not in head:
            raise ValueError(f"{os.path.basename(path)} is not a STEP file (missing ISO-10303-21 header)")
        if "DATA;" not in head and size < 4096:
            raise ValueError(f"{os.path.basename(path)} has no DATA section")
    return ext


def read_shape(path):
    """Read a CAD file into one shape. Returns ``(shape, warnings)``."""
    _check_file(path)
    warnings = []
    shape = Part.Shape()
    try:
        shape.read(path)
    except Exception as e:
        raise ValueError(f"could not read {os.path.basename(path)}: {e}") from e
    if shape.isNull() or not shape.Faces:
        raise ValueError(f"{os.path.basename(path)} contains no B-rep geometry (no faces); "
                         "it may hold only wireframe, PMI or product structure")
    if not shape.isValid():
        try:
            fixed = shape.copy()
            fixed.fix(1e-6, 1e-6, 1e-4)
            if fixed.isValid():
                shape = fixed
                warnings.append("input B-rep was invalid and has been repaired")
            else:
                warnings.append("input B-rep is invalid; projection may show artefacts")
        except Exception:
            warnings.append("input B-rep is invalid; projection may show artefacts")
    if not shape.Solids:
        solids = []
        for sh in shape.Shells:
            try:
                if sh.isClosed():
                    solids.append(Part.Solid(sh))
            except Exception:
                pass
        if solids:
            shape = Part.makeCompound(solids) if len(solids) > 1 else solids[0]
            warnings.append("surface model: closed shells were converted to solids")
        else:
            warnings.append("surface model without closed shells: drawn as surfaces, hole detection limited")
    bb = shape.BoundBox
    if bb.DiagonalLength < 1e-6:
        raise ValueError("geometry has zero size")
    return shape, warnings


# --------------------------------------------------------------- alignment
def _is_principal(v, tol=1e-4):
    return max(abs(v.x), abs(v.y), abs(v.z)) > 1 - tol


def _dominant_normals(shape):
    """Area-weighted planar normal clusters (sign-insensitive), largest first."""
    clusters = []
    for f in shape.Faces:
        if type(f.Surface).__name__ != "Plane":
            continue
        n = App.Vector(f.Surface.Axis)
        if n.Length < 1e-12:
            continue
        n.normalize()
        for c in clusters:
            if abs(abs(c[0].dot(n)) - 1) < 1e-4:
                c[1] += f.Area
                break
        else:
            clusters.append([n, f.Area])
    clusters.sort(key=lambda c: -c[1])
    return clusters


def _cylinder_axes(shape):
    axes = []
    for f in shape.Faces:
        if type(f.Surface).__name__ in ("Cylinder", "Cone"):
            a = App.Vector(f.Surface.Axis)
            a.normalize()
            for c in axes:
                if abs(abs(c[0].dot(a)) - 1) < 1e-4:
                    c[1] += f.Area
                    break
            else:
                axes.append([a, f.Area])
    axes.sort(key=lambda c: -c[1])
    return axes


def _inertia_axes(shape):
    try:
        import numpy as np

        m = shape.MatrixOfInertia if shape.Solids else Part.makeCompound(shape.Faces).MatrixOfInertia
        a = np.array([[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]])
        w, v = np.linalg.eigh(a)
        return [App.Vector(*v[:, i]) for i in range(3)]
    except Exception:
        return [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)]


def canonical_rotation(shape):
    """Rotation that brings the part's own axes onto X/Y/Z, or None if aligned.

    Primary axis: normal of the largest planar area (else the main cylinder
    axis, else the inertia axis); secondary: the next planar normal (or
    cylinder axis) perpendicular to it.
    """
    cands = [c[0] for c in _dominant_normals(shape)] + [c[0] for c in _cylinder_axes(shape)]
    if not cands:
        cands = _inertia_axes(shape)
    e3 = cands[0]
    e1 = next((c for c in cands[1:] if abs(c.dot(e3)) < 1e-4), None)
    if e1 is None:
        # any vector perpendicular to e3, preferring a global axis
        for g in (App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)):
            t = g - e3 * g.dot(e3)
            if t.Length > 1e-3:
                e1 = t
                break
    e1.normalize()
    if _is_principal(e3) and _is_principal(e1):
        return None
    e2 = e3.cross(e1)
    e2.normalize()
    # keep the larger extent of the plane along X (readability)
    m = App.Matrix(e1.x, e1.y, e1.z, 0, e2.x, e2.y, e2.z, 0, e3.x, e3.y, e3.z, 0, 0, 0, 0, 1)
    return App.Rotation(m)


def align(shape):
    """Return ``(aligned_shape, rotation_or_None)``."""
    rot = canonical_rotation(shape)
    if rot is None:
        return shape, None
    s = shape.copy()
    s.rotate(App.Vector(0, 0, 0), rot.Axis, math.degrees(rot.Angle))
    # snap residual numeric noise: re-check, else accept
    return s, rot


# --------------------------------------------------------------- assemblies
def _signature(solid):
    bb = solid.BoundBox
    dims = sorted((round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)))
    return (round(solid.Volume, 1), tuple(dims), len(solid.Faces))


def component_names(path):
    """Names of leaf parts from the STEP product structure, in file order."""
    names = []
    try:
        import Import  # noqa: F401  (OCAF reader; available in console mode)

        active = App.ActiveDocument
        try:
            tmp = App.newDocument("FDImportTmp", hidden=True)
        except TypeError:
            tmp = App.newDocument("FDImportTmp")
        try:
            Import.insert(path, tmp.Name)
            for o in tmp.Objects:
                if o.TypeId == "Part::Feature" and getattr(o, "Shape", None) is not None and o.Shape.Solids:
                    names.append((o.Label, o.Shape.copy()))
        finally:
            App.closeDocument(tmp.Name)
            if active is not None:
                App.setActiveDocument(active.Name)
    except Exception:
        return []
    return names


def components(shape, path=None):
    """Group the solids of an assembly: ``[{"item", "qty", "name", "solids": [...]}]``."""
    solids = list(shape.Solids)
    if len(solids) < 2:
        return []
    named = component_names(path) if path and os.path.splitext(path)[1].lower() in STEP_EXT else []
    groups = {}
    order = []
    for i, s in enumerate(solids):
        sig = _signature(s)
        if sig not in groups:
            groups[sig] = {"solids": [], "name": None}
            order.append(sig)
        groups[sig]["solids"].append(i)
        if groups[sig]["name"] is None:
            for label, ns in named:
                try:
                    if _signature(ns) == sig:
                        groups[sig]["name"] = label
                        break
                except Exception:
                    continue
    out = []
    for n, sig in enumerate(order, 1):
        g = groups[sig]
        out.append({"item": n, "qty": len(g["solids"]), "name": g["name"] or f"Part {n}", "solids": g["solids"]})
    return out
