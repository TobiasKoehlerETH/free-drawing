# Drawing validation — 2026-10-17

Environment: Windows / FreeCAD 1.1. This is implementation evidence, not a standards certificate.

## Broad corpus

The batch created and inspected **36 unique part drawings**: 28 existing samples and 8 new stress shapes. It produced 14 sections and 20 details. Inputs include plates, bolt circles, turned parts, threaded parts, pockets, counterbores, cross holes, slots, seven sheet-metal forms, assemblies, a large frame, a tiny pin, a blind manifold, rotated geometry, an inclined bore, a loft, a ribbed bracket and a stepped housing.

Command used:

```powershell
uv run python samples/run_samples.py --extra samples/complex --out samples/validation --jobs 2
```

Local evidence: `samples/validation/summary.json`, per-part snapshots, reports, FCStd documents and PNGs. The initial summary contains 44 rows due to Windows case-insensitive glob duplication. It represents 36 unique sources, not 44 different test shapes. The collector was fixed to enumerate once and deduplicate resolved paths.

This batch spanned active development. Preserve it as diagnostic evidence; do not present it as a qualification run against one frozen revision. Later checks below verify affected changes.

## Follow-up checks

- Counterbore rerun: section A–A renders with voids unhatched; the hidden-line dimension warning is resolved after re-planning.
- Flange rerun: the outside diameter and pitch-circle diameter survive recompute/dedupe; E.1 no longer fails for those missing sizes.
- Micro-groove rerun: an enlarged detail is generated with dimensions reassigned to its geometry.
- Supplementary FreeCAD regression tests create drawings and reopen saved files in another process; check first/third-angle counterbores, tube half-section geometry, micro-groove details and flange dimensions.
- Final targeted corpus checks cover oblique bore, lofted adapter, mixed assembly, stepped housing and rotated manifold with the final coverage-gap and assembly-section policy.

These cases still need engineering specification: the test inputs deliberately do not supply material or tolerance values. Their F.1 and K.1 failures are expected and do not indicate a failed file export.

## Outstanding findings

| Family | Remaining issue |
|---|---|
| Plate / brackets | Some dimensions overlap the title block or part geometry; layout needs correction and reinspection |
| Blind / rotated manifold | Crowded callout intersects another dimension line |
| Inclined bore | Automatic auxiliary view and full diameter/location definition are not implemented; explicitly reported |
| Loft | Freeform/profile definition and some overall positions remain unresolved; explicitly reported |
| Ribbed bracket / 135° sheet bend | Inclined-face angle coverage remains incomplete |
| Stepped housing | Boss-centre location requirements remain unplaced |
| Modelled threads | Some thread extent, symbol classification and collision findings remain |
| Assembly | Selective sectioning, component exclusions and adjacent-part hatching require explicit review |

All 36 files were generated, but they are **not all manufacturing-ready drawings**. Visual inspection covered the counterbore, tube, flange-related measurements and micro-groove detail; it did not certify every sheet or every export backend.

## Source verification

Six downloaded reference PDFs were reopened with a PDF parser; their headers, page counts and SHA-256 hashes are recorded in `guidelines/sources/manifest.json`. They comprise a 19-page Normen-Auszug 2026 reading sample, a one-page SNV learning reference, and ISO previews of 9, 15, 4 and 15 pages. No complete freely licensed TPD/GPS standards collection was established.
