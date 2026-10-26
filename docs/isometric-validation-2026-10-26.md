# Multiple-part isometric validation — 26 October 2026

Tested 12 distinct STEP models with the current default generator, A3 sheets and first-angle projection. Each saved FCStd was reopened in a fresh FreeCAD process before PDF and PNG export. The engine remained unchanged throughout the run; source files and saved drawings were unchanged by the reopen/export checks.

All 12 retained one coloured isometric at the bottom right above the title block, clear of defining views, dimension text and dimension lines. Every defining view carried dimensions; standard views shared a scale. All dimension values and references survived reopening. Poppler renders independently confirmed coloured pixels in the isometric area of every PDF.

## Findings

- **Counterbored block:** the planned section did not fit (D.1 failure); hidden-line dimensions remain. The isometric placement itself passes. This drawing needs further work.
- **Tiny pin:** the isometric is present and coloured, but visually very small at the fixed 1:2 recognition scale. Standard views are enlarged to 10:1.
- **Assembly visual review:** component balloons appear detached from the geometry near the lower-left corner, and the parts list uses imported STEP translator names. These need cleanup even though the isometric and automated placement checks pass.
- **Large frame:** the standard view uses 1:5 and the isometric uses 1:10 to fit; this raises the existing C.7 scale warning.
- **Rotated manifold:** hidden-line dimension warnings remain. The assembly has a front-view visibility warning. Some cylindrical cases have long-extension-line warnings. The sheet tray lacks a supplied bend-table/K-factor basis.
- Material and tolerance inputs are absent in these sample models; their findings remain in the individual reports. These checks do not certify manufacturing completeness.

| Part | Family | Standard scale | Dimensions | Isometric/persistence checks |
| --- | --- | --- | --- | --- |
| [counterbored_block](../samples/validation/isometric-multiple-parts-2026-10-26/counterbored_block.pdf) | counterbores and section | 2 : 1 | 8 | PASS |
| [flange](../samples/validation/isometric-multiple-parts-2026-10-26/flange.pdf) | flange and pitch circle | 1 : 1 | 4 | PASS |
| [large_frame](../samples/validation/isometric-multiple-parts-2026-10-26/large_frame.pdf) | large part requiring reduction | 1 : 5 | 6 | PASS |
| [micro_grooves](../samples/validation/isometric-multiple-parts-2026-10-26/micro_grooves.pdf) | fine turned features | 5 : 1 | 9 | PASS |
| [mixed_assembly](../samples/validation/isometric-multiple-parts-2026-10-26/mixed_assembly.pdf) | multiple components | 1 : 1 | 3 | PASS |
| [plate_hole_pattern](../samples/validation/isometric-multiple-parts-2026-10-26/plate_hole_pattern.pdf) | hole-pattern plate | 1 : 1 | 10 | PASS |
| [rotated_manifold](../samples/validation/isometric-multiple-parts-2026-10-26/rotated_manifold.pdf) | rotated complex solid | 1 : 1 | 14 | PASS |
| [sheet_tray](../samples/validation/isometric-multiple-parts-2026-10-26/sheet_tray.pdf) | bent sheet metal | 1 : 1 | 17 | PASS |
| [slotted_plate](../samples/validation/isometric-multiple-parts-2026-10-26/slotted_plate.pdf) | slots and radii | 2 : 1 | 11 | PASS |
| [tiny_pin](../samples/validation/isometric-multiple-parts-2026-10-26/tiny_pin.pdf) | small part requiring enlargement | 10 : 1 | 6 | PASS |
| [tube](../samples/validation/isometric-multiple-parts-2026-10-26/tube.pdf) | hollow cylindrical part | 2 : 1 | 3 | PASS |
| [turned_shaft](../samples/validation/isometric-multiple-parts-2026-10-26/turned_shaft.pdf) | turned shaft | 2 : 1 | 7 | PASS |

[PDF overview](../samples/validation/isometric-multiple-parts-2026-10-26/pdf-overview.png) · [Detailed results](../samples/validation/isometric-multiple-parts-2026-10-26/results.json) · [PDF colour checks](../samples/validation/isometric-multiple-parts-2026-10-26/pdf-colour-check.json)

Repeat with `uv run python samples/validate_view_policy.py --out samples/validation/new-isometric-check --jobs 2`. Use a new or empty output directory. `--names` selects specific cases for a focused rerun.
