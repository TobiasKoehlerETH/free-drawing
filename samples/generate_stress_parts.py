"""Additional deterministic geometry corpus, executed inside FreeCADCmd.

From the repository root::

    freecadcmd -c "exec(open('samples/generate_stress_parts.py').read())"

Synthetic nominal millimetre geometry only; no manufacturing specification is
implied. The manifest records checks before and after STEP export. Existing
base/complex samples are never touched. All source names use a stress_ prefix
so ``run_samples.py stress_ --extra samples/stress --out <new-folder>`` selects
only these sources, even though the batch runner always discovers base parts.
"""

import hashlib
import json
import math
from pathlib import Path

import FreeCAD as App
import Part

V = App.Vector
ROOT = Path.cwd()
OUTPUT = ROOT / "samples" / "stress"


def nested_cavity():
    body = Part.makeBox(96, 76, 52)
    body = body.cut(Part.makeBox(80, 60, 38, V(8, 8, 14)))
    body = body.cut(Part.makeBox(52, 34, 8, V(22, 21, 6)))
    body = body.fuse(Part.makeCylinder(10, 22, V(48, 38, 6)))
    body = body.cut(Part.makeCylinder(4, 22, V(48, 38, 6)))
    for x, y in ((4, 4), (92, 4), (4, 72), (92, 72)):
        body = body.cut(Part.makeCylinder(1.5, 18, V(x, y, 34)))
    return body.removeSplitter()


def skew_passages():
    body = Part.makeBox(84, 68, 56)
    # The axes meet at the block centre; none is a principal-axis cylinder.
    centre = V(42, 34, 28)
    for radius, direction in ((5, V(1, 0.35, 0.4)),
                              (3.5, V(-0.3, 1, 0.6)),
                              (2.5, V(0.45, -0.25, 1))):
        direction.normalize()
        body = body.cut(Part.makeCylinder(radius, 200,
                                         centre - direction * 100, direction))
    return body


def spline_loft():
    profiles = []
    for z, radius, shift, twist in ((0, 24, 0, 0), (18, 18, 5, 0.2),
                                   (42, 20, 12, 0.55), (66, 12, 18, 0.8)):
        points = []
        for i in range(12):
            theta = i * math.pi / 6
            r = radius * (1 + 0.12 * math.cos(3 * theta))
            points.append(V(shift + r * math.cos(theta + twist),
                            0.8 * r * math.sin(theta + twist), z))
        curve = Part.BSplineCurve()
        curve.interpolate(points, PeriodicFlag=True)
        profiles.append(Part.Wire([curve.toShape()]))
    return Part.makeLoft(profiles, True, False)


def curved_tube():
    outer = Part.makeTorus(35, 8, V(0, 0, 0), V(0, 0, 1), 0, 360, 120)
    inner = Part.makeTorus(35, 4, V(0, 0, 0), V(0, 0, 1), 0, 360, 120)
    return outer.cut(inner)


def slender_shaft():
    axis = V(1, 0, 0)
    body = Part.makeCylinder(4, 850, V(0, 0, 0), axis)
    body = body.fuse(Part.makeCone(7, 4, 20, V(0, 0, 0), axis))
    body = body.fuse(Part.makeCylinder(6, 12, V(838, 0, 0), axis))
    for x in (26, 822):
        ring = Part.makeCylinder(5, 0.7, V(x, 0, 0), axis).cut(
            Part.makeCylinder(3.4, 0.7, V(x, 0, 0), axis))
        body = body.cut(ring)
    return body.removeSplitter()


def large_support():
    body = Part.makeBox(2400, 1400, 120)
    for x in (400, 1800):
        column = Part.makeBox(200, 300, 500, V(x, 550, 120))
        column = column.cut(Part.makeBox(160, 260, 500, V(x + 20, 570, 120)))
        body = body.fuse(column)
    for x in (120, 1200, 2280):
        for y in (120, 1280):
            body = body.cut(Part.makeCylinder(18, 120, V(x, y, 0)))
    return body.removeSplitter()


def dense_pattern():
    body = Part.makeBox(110, 85, 8)
    for i in range(8):
        for j in range(6):
            x, y = 13 + 12 * i, 12.5 + 12 * j
            r = 1.6 if (i + j) % 2 else 2.1
            body = body.cut(Part.makeCylinder(r, 8, V(x, y, 0)))
            if (i + j) % 3 == 0:
                body = body.cut(Part.makeCone(r, r + 1.8, 1.8, V(x, y, 6.2)))
    return body


def mixed_scale_assembly():
    # Disjoint components retain identity. The clearance to every pin and
    # washer is intentional; these are not a fused assembly model.
    frame = Part.makeBox(180, 100, 12).cut(Part.makeBox(140, 60, 12, V(20, 20, 0)))
    pieces = [frame]
    for x in (52, 128):
        pieces.append(Part.makeCylinder(18, 46, V(x, 27, 32), V(0, 1, 0)))
        pieces.append(Part.makeCylinder(2, 76, V(x, 12, 32), V(0, 1, 0)))
    for x in (10, 170):
        for y in (10, 90):
            pieces.append(Part.makeCylinder(1.2, 8, V(x, y, 16)))
    washer = Part.makeCylinder(3, 0.6, V(92, 50, 15)).cut(
        Part.makeCylinder(1.6, 0.6, V(92, 50, 15)))
    pieces.append(washer)
    # Bore both rollers to avoid physically overlapping independent solids.
    for index, x in ((1, 52), (3, 128)):
        pieces[index] = pieces[index].cut(
            Part.makeCylinder(2.5, 46, V(x, 27, 32), V(0, 1, 0)))
    return Part.makeCompound(pieces)


def splined_coupling():
    points = []
    for i in range(24):
        for offset, radius in ((0, 22), (0.18, 26), (0.65, 26), (0.83, 22)):
            angle = (i + offset) * math.pi / 12
            points.append(V(radius * math.cos(angle), radius * math.sin(angle), 0))
    points.append(points[0])
    body = Part.Face(Part.makePolygon(points)).extrude(V(0, 0, 35))
    body = body.cut(Part.makeCylinder(9, 35))
    return body.cut(Part.makeBox(5, 5, 35, V(7, -2.5, 0)))


def curved_rib_shell():
    body = Part.makeBox(90, 65, 5)
    body = body.fuse(Part.makeBox(90, 5, 36, V(0, 0, 5)))
    rib = Part.makeCylinder(32, 29, V(45, 5, 5)).cut(
        Part.makeCylinder(29, 29, V(45, 5, 5)))
    rib = rib.common(Part.makeBox(90, 60, 29, V(0, 5, 5)))
    body = body.fuse(rib)
    for x, y in ((10, 50), (80, 50)):
        body = body.cut(Part.makeCylinder(3, 5, V(x, y, 0)))
    return body.removeSplitter()


CASES = [
    ("nested_cavity", nested_cavity, 1, "Two pocket depths, recessed hollow boss, four blind rim holes"),
    ("skew_passages", skew_passages, 1, "Three intersecting bores with non-principal axes"),
    ("spline_loft", spline_loft, 1, "Four twisted periodic spline profiles and varying cross-section"),
    ("curved_tube", curved_tube, 1, "120 degree toroidal tube, 16 outside/8 inside diameter"),
    ("slender_shaft", slender_shaft, 1, "850 mm shaft, taper, end collar, two 0.7 mm grooves"),
    ("large_support", large_support, 1, "2400 mm base with two hollow towers and six through holes"),
    ("dense_pattern", dense_pattern, 1, "48 holes, alternating diameters, 16 conical recesses"),
    ("mixed_scale_assembly", mixed_scale_assembly, 10, "Ten separate solids from a 180 mm frame to a 0.6 mm washer"),
    ("splined_coupling", splined_coupling, 1, "24 straight-sided external teeth with through bore and keyway"),
    ("curved_rib_shell", curved_rib_shell, 1, "Curved thin rib joined to base and back wall"),
]


FEATURE_FACTS = {
    "nested_cavity": {"overall_mm": [96, 76, 52], "upper_pocket_xy_mm": [80, 60],
                      "upper_pocket_depth_mm": 38, "lower_pocket_xy_mm": [52, 34],
                      "lower_pocket_extra_depth_mm": 8, "boss_od_id_mm": [20, 8],
                      "boss_top_z_mm": 28, "rim_holes_count": 4,
                      "rim_holes_diameter_mm": 3, "rim_holes_depth_mm": 18},
    "skew_passages": {"overall_mm": [84, 68, 56], "bore_diameters_mm": [10, 7, 5],
                      "bore_meeting_point_mm": [42, 34, 28],
                      "bore_axis_direction_ratios": [[1, 0.35, 0.4], [-0.3, 1, 0.6], [0.45, -0.25, 1]]},
    "spline_loft": {"section_z_mm": [0, 18, 42, 66], "base_radii_mm": [24, 18, 20, 12],
                    "section_x_shift_mm": [0, 5, 12, 18], "section_twist_rad": [0, 0.2, 0.55, 0.8],
                    "profile_definition": "Periodic interpolating B-spline through 12 polar samples; r=base_r*(1+0.12*cos(3*theta)); y multiplier 0.8"},
    "curved_tube": {"centreline_radius_mm": 35, "outside_diameter_mm": 16,
                    "inside_diameter_mm": 8, "sweep_angle_degrees": 120, "wall_thickness_mm": 4},
    "slender_shaft": {"overall_length_mm": 850, "body_diameter_mm": 8,
                      "taper_end_diameters_mm": [14, 8], "taper_length_mm": 20,
                      "end_collar_diameter_mm": 12, "end_collar_length_mm": 12,
                      "groove_start_x_mm": [26, 822], "groove_width_mm": 0.7,
                      "groove_root_diameter_mm": 6.8},
    "large_support": {"base_mm": [2400, 1400, 120], "towers_count": 2,
                      "tower_outer_xy_mm": [200, 300], "tower_inner_xy_mm": [160, 260],
                      "tower_height_mm": 500, "holes_count": 6, "holes_diameter_mm": 36,
                      "hole_x_mm": [120, 1200, 2280], "hole_y_mm": [120, 1280]},
    "dense_pattern": {"overall_mm": [110, 85, 8], "hole_grid_xy": [8, 6],
                      "holes_count": 48, "hole_diameters_mm": [3.2, 4.2],
                      "hole_count_each_diameter": [24, 24], "grid_pitch_mm": [12, 12],
                      "first_hole_xy_mm": [13, 12.5], "conical_recesses_count": 16,
                      "conical_recess_depth_mm": 1.8, "conical_recess_outer_diameters_mm": [6.8, 7.8],
                      "conical_recess_included_angle_degrees": 90},
    "mixed_scale_assembly": {"solids_count": 10, "frame_outer_mm": [180, 100, 12],
                             "frame_opening_xy_mm": [140, 60], "rollers_count": 2,
                             "roller_od_id_length_mm": [36, 5, 46], "axles_count": 2,
                             "axle_diameter_length_mm": [4, 76], "small_pins_count": 4,
                             "small_pin_diameter_length_mm": [2.4, 8],
                             "washer_od_id_thickness_mm": [6, 3.2, 0.6]},
    "splined_coupling": {"teeth_count": 24, "major_minor_diameter_mm": [52, 44],
                         "length_mm": 35, "bore_diameter_mm": 18,
                         "keyway_width_mm": 5, "keyway_outer_x_mm": 12,
                         "tooth_description": "Synthetic straight-sided radial teeth; not involute gear teeth"},
    "curved_rib_shell": {"base_mm": [90, 65, 5], "back_wall_thickness_mm": 5,
                         "overall_height_mm": 41, "rib_inner_outer_radii_mm": [29, 32],
                         "rib_height_mm": 29, "holes_count": 2, "holes_diameter_mm": 6},
}


def metrics(shape):
    # OCCT's conservative BoundBox can expand after STEP import around tilted
    # trimmed cylinders or spline faces. Use the precise shape bounds when
    # checking nominal dimensions, retaining the coarse box for diagnostics.
    box = shape.optimalBoundingBox()
    coarse = shape.BoundBox
    surfaces = {}
    for face in shape.Faces:
        name = type(face.Surface).__name__
        surfaces[name] = surfaces.get(name, 0) + 1
    return {"valid": shape.isValid(), "closed": shape.isClosed(),
            "solids": len(shape.Solids), "faces": len(shape.Faces),
            "edges": len(shape.Edges), "volume_mm3": shape.Volume,
            "bounds_mm": [box.XLength, box.YLength, box.ZLength],
            "coarse_bounds_mm": [coarse.XLength, coarse.YLength, coarse.ZLength],
            "surfaces": surfaces}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"units": "mm", "purpose": "Synthetic drawing stress tests; no manufacturing specification",
                "generator": "samples/generate_stress_parts.py", "freecad_version": App.Version(),
                "cases": []}
    for short_name, factory, solids, features in CASES:
        name = "stress_" + short_name
        shape = factory()
        before = metrics(shape)
        if not before["valid"] or not before["closed"] or before["solids"] != solids:
            raise ValueError(f"Invalid source {name}: {before}")
        path = OUTPUT / (name + ".step")
        shape.exportStep(str(path))
        imported = Part.Shape()
        imported.read(str(path))
        after = metrics(imported)
        volume_match = abs(after["volume_mm3"] - before["volume_mm3"]) <= max(1e-5, shape.Volume * 1e-8)
        bounds_match = all(abs(a - b) <= 1e-5 for a, b in zip(before["bounds_mm"], after["bounds_mm"]))
        if not after["valid"] or after["solids"] != solids or not volume_match or not bounds_match:
            raise ValueError(f"STEP round trip failed for {name}: {after}")
        entry = {"name": name, "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                 "features": features, "expected_solids": solids,
                 "feature_facts": FEATURE_FACTS[short_name],
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "source_geometry": before, "step_geometry": after,
                 "roundtrip_volume_matches": volume_match, "roundtrip_bounds_match": bounds_match}
        manifest["cases"].append(entry)
        print(f"{name}: valid, {solids} solids, {after['faces']} faces, {after['bounds_mm']} mm")
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


main()
