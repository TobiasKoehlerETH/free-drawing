# Machining feature validation, 2026-10-29

The drawing generator now preserves the entry face of blind holes and one-sided counterbores or countersinks. Equal sizes on opposite faces no longer collapse into one callout. This improves geometric definition; manufacturing approval still requires the designer's material, tolerances, functional requirements, and review.

## Changes

- Hole grouping includes entry direction. The planner selects the entrance view or an actual section and can add a rear projection.
- Rear projections sit beyond the adjacent side view. FreeCAD places its native rear projection on the right in both projection conventions; the layout and inspector now follow that behavior. See the [FreeCAD 1.1 projection-group implementation](https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/TechDraw/App/DrawProjGroup.cpp#L583).
- Profile diameter witnesses remain within the corresponding cylinder's axial extent. A matching dimension on a different part of the outline cannot substitute for it.
- A concentric boss shares the enclosing cylinder's axis. Offset bosses retain their location requirements.
- Baseline locations on circular outlines use exact extremum points. Cosmetic witnesses use verified view coordinates, avoiding a mirrored vertical coordinate observed with the 3D insertion API. The same helper serves centres, thread ends, and reconstruction repairs.
- Inspection recognizes points on visible curves even when those points are not topological vertices. Points on hidden geometry remain subject to review.
- Thread callouts no longer invent `6H` or `6g`. An explicitly supplied class is retained; a missing class is reported. Internal thread depth is checked even without a class.

## Tests

Three new real-FreeCAD regressions failed before implementation: opposite entry grouping, redundant concentric-boss location, and inferred thread class.

The final Python run passed **236 tests**, with two existing Qt deprecation warnings. The broad native run passed all **37 existing FreeCAD tests** and exposed two failures in the new cases. After correcting rear placement and circular-witness visibility, the focused run passed all **8 new FreeCAD tests** and the **12 policy tests** run alongside them. This is coverage across the broad run and focused reruns, not a claim that the first broad run was clean.

The new tests check first- and third-angle drawings after reopening, measured hole locations, diameter witness positions, and cosmetic vertices in six directions at three scales. Repeated vertex insertion must reuse the same reference.

## Reproduce

```powershell
uv run --group dev pytest -q
uv run python samples/audit_production.py --out samples/validation/new-production-audit --jobs 1
```

The audit generates five machining STEP cases and seven existing samples, reopens every drawing in a fresh process, and exports PDFs and previews. Its JSON separates source preservation, dimension persistence, view policy, layout findings, and unresolved engineering requirements. Use a new output directory for every run.

## Final drawing audit

All 12 drawings reopened with unchanged dimension values and unchanged input hashes. All view-policy checks passed. No unplaced requirements or layout findings remained. All 12 exported PDFs were rendered with Poppler and visually reviewed. The engine hashes stayed unchanged throughout this final audit.

| Part | Scale | Dimensions | Unplaced | Layout findings |
| --- | --- | ---: | ---: | ---: |
| coaxial_blind_holes | 2 : 1 | 7 | 0 | 0 |
| concentric_boss | 2 : 1 | 7 | 0 | 0 |
| counterbored_block | 2 : 1 | 7 | 0 | 0 |
| flange | 1 : 1 | 5 | 0 | 0 |
| opposed_blind_holes | 1 : 1 | 9 | 0 | 0 |
| opposed_counterbores | 1 : 1 | 9 | 0 | 0 |
| opposed_side_holes | 2 : 1 | 9 | 0 | 0 |
| plate_hole_pattern | 1 : 1 | 11 | 0 | 0 |
| rotated_manifold | 1 : 1 | 13 | 0 | 0 |
| slotted_plate | 1 : 1 | 12 | 0 | 0 |
| threaded_shaft | 2 : 1 | 5 | 0 | 0 |
| turned_shaft | 2 : 1 | 7 | 0 | 0 |

Remaining findings are retained in the evidence:

- **C.1**: front view ProjItem shows more hidden than visible edges. Cases: concentric_boss, counterbored_block, flange, opposed_blind_holes, opposed_counterbores, rotated_manifold.
- **E.2**: very long extension lines - dimension far from its feature. Cases: threaded_shaft, turned_shaft.
- **E.5**: dimensions attached to hidden lines. Cases: rotated_manifold, threaded_shaft.
- **J.2**: incomplete metric callout(s): M20×2.5. Cases: threaded_shaft.

All sheets remain **In preparation** because material and tolerances were not supplied. The audit does not qualify arbitrary STEP geometry or replace engineering approval. Auxiliary/offset sections, complex stepped bores, freeform definitions, and functional datum selection remain outside this change.

[Machine-readable evidence](production-drawing-validation-2026-10-29.json). [Drawing files and individual findings](../samples/validation/production-final-2026-10-29/summary.md). The local artifact directory contains the five generated STEP cases and each editable FCStd, PDF, preview, and snapshot. The evidence records the paths and hashes of all input STEP files.

