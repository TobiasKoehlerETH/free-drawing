# Additional offline drawing stress corpus, 2026-10-18

This corpus supplies ten synthetic nominal solids/assemblies for automated
drawing evaluation. It adds geometry families beyond the 28 base and eight
existing complex samples. Geometry is in millimetres. No material, tolerances,
datums, finish, thread specification, or release approval is implied.

## Generate and validate sources

From the repository root, run in FreeCADCmd:

```powershell
& 'C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe' -c "exec(open('samples/generate_stress_parts.py', encoding='utf-8').read())"
```

The generator writes `samples/stress/stress_*.step` and
`samples/stress/manifest.json`. All ten generated shapes and STEP imports were
valid and closed in FreeCAD 1.1.1. The generator checks expected solid counts,
round-trip volume, and precise shape dimensions. The manifest preserves source
hashes, before/after metrics, surface families, and explicit construction facts
for checking dimension coverage independently of the drawing inspector.

| Source suffix | Deliberate challenge | Solids | Faces after import | Precise bounds, mm |
|---|---|---:|---:|---|
| nested_cavity | Two pocket depths, hollow recessed boss, four blind rim holes | 1 | 28 | 96 × 76 × 52 |
| skew_passages | Three intersecting bores with non-principal axes | 1 | 11 | 84 × 68 × 56 |
| spline_loft | Four twisted periodic spline profiles, changing cross-section | 1 | 3 | 55.613 × 40.344 × 66 |
| curved_tube | Hollow 120° toroidal sweep | 1 | 4 | 64.5 × 43 × 16 |
| slender_shaft | 850 mm length, taper, collar, two 0.7 mm grooves | 1 | 14 | 850 × 14 × 14 |
| large_support | 2.4 m base, hollow towers, large mounting-hole pattern | 1 | 32 | 2400 × 1400 × 620 |
| dense_pattern | 48 alternating-size bores, 16 conical recesses | 1 | 70 | 110 × 85 × 8 |
| mixed_scale_assembly | Ten separate parts, 180 mm frame to 0.6 mm washer | 10 | 40 | 180 × 100 × 50 |
| splined_coupling | 24 straight-sided radial teeth, bore and keyway | 1 | 102 | 51.942 × 51.942 × 35 |
| curved_rib_shell | Semicircular thin rib joined to base and back wall | 1 | 14 | 90 × 65 × 41 |

The source geometry is deterministic. STEP file headers may contain export
timestamps, so reproducible geometry does not imply byte-identical STEP files.
The existing base/complex source files are unchanged.

`Shape.BoundBox` is conservative around some trimmed curved surfaces. For the
skew passages, STEP import reports coarse bounds approximately
93.433 × 75.829 × 60.594 mm, whereas `optimalBoundingBox()` correctly returns
84 × 68 × 56 mm. Both bounds are recorded. Source round-trip checks use the
precise bounds rather than mistaking the conservative bounds for a shape change.

## Exploratory automatic run

The initial command was:

```powershell
uv run python samples/run_samples.py stress_ --extra samples/stress --out samples/validation-2026-10-18/stress --jobs 1
```

The `stress_` name filter excludes the base sources that the runner otherwise
always discovers. No per-case view choices, manual dimensions, post-generation
repairs, or model-dependent agent input were supplied. These outputs used the
automatic view defaults.

The run was deliberately interrupted after the user changed the final
evaluation requirements to fixed Front/Top/Right views and corrected centerline
rendering. The coordinator will run the final evaluation against frozen code.
The partial run is exploratory evidence only, not final qualification.

Completed exploratory artifacts are in `samples/validation-2026-10-18/stress/`:

- `stress_curved_rib_shell`: FCStd, PNG, snapshot JSON, inspection text.
- `stress_curved_tube`: FCStd, PNG, snapshot JSON, inspection text.
- `exploratory_run_status.json`: interruption reason and verified process IDs.

No PDFs were exported and the saved FCStd documents were not edited. The dense
pattern was still generating when interrupted; this is not a failure or timeout
result for that case. Seven later cases were not attempted in this partial run.

## Initial feature and visual observations

The curved rib shell receives 13 dimensions, including R29, R32, 2× diameter 6,
overall sizes 90/65/41, and the rib's top elevation 34. Its report has only the
expected missing material and tolerance failures, score 95, no warnings, and no
coverage gaps. Nevertheless, the rendered Detail A radius leaders point into
empty space. The R29 arrow endpoint `[141.56, 256.27]` is approximately 19.85 mm
from the nearest visible polyline, and the R32 endpoint `[187.63, 216.34]` is
15.06 mm away. Both lie outside Detail A's visible-geometry bounds. This is a
visible annotation defect that the numeric coverage and automated inspection
do not identify.

The curved tube receives only four dimensions: overall thickness 16, R4, R8,
and angle 120°. Its known centreline radius is 35 mm, and no R35 dimension is
present. The inspector reports unplaced X/Y overall dimensions but records no
explicit surface coverage gap. Correct nominal values for a few features do
not establish complete geometry definition.

These observations concern the saved exploratory snapshots and PNGs only.
Final-code results and exported-PDF appearance require separate verification.
