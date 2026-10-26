# Drawing reference validation — 26 October 2026

Historical run: the measurements below precede the user's correction to retain the coloured isometric at the bottom right. Current generation keeps that recognition view without dimensions and reserves its space during layout. The dimension requirement applies to standard, section and detail views.

Six preselected paired STEP models from the pinned Cadsy dataset were generated with a frozen engine and reopened in fresh FreeCAD processes. All six pass the checked layout rules: page bounds/title-block clearance, shared standard-view scale, dimensioned views, annotation clearance, and duplicate dimensions.

The view count fell from 38 to 20. All 130 dimensions were retained; 126 are on standard views. No visible view is undimensioned. The selected cases require three sections in total and no details.

| Part | Views before → after | Shared scale before → after | Dimensions | Sections / details |
| --- | --- | --- | --- | --- |
| part_0001 | 7 → 4 | 1 : 5 → 1 : 1 | 26 | 1 / 0 |
| part_0013 | 5 → 2 | 1 : 1 → 2 : 1 | 14 | 0 / 0 |
| part_0026 | 7 → 3 | 1 : 1 → 1 : 2 | 36 | 0 / 0 |
| part_0039 | 6 → 3 | 1 : 1 → 1 : 1 | 7 | 1 / 0 |
| part_0052 | 6 → 5 | 1 : 1 → 1 : 2 | 24 | 1 / 0 |
| part_0065 | 7 → 3 | 1 : 1 → 1 : 1 | 23 | 0 / 0 |

The generator tests larger preferred scales before reducing a drawing. Two dense cases need 1:2 on the selected A3 template when dimensions are retained on the standard views. Text remains 3.5 mm and dimension strokes remain 0.35 mm; the layout does not shrink them.

Validation: 219 tests passed, including 17 real FreeCAD tests. The final six PDFs were rendered with Poppler and visually checked. PDF export uses the schematic renderer; native TechDraw GUI caption placement is not certified by this check.

Two source models still need geometric definition work: part_0052 has two unresolved boss-centre positions and three dimensions on hidden geometry; part_0065 has an unresolved chamfer and inclined-face angles. All cases also lack supplied material and general tolerance values. Those findings remain visible; these are draft drawings, not manufacturing release approvals.

The before/after comparison preserves 129 of 130 nominal numbers. On part_0065, one angle changes from 45° to its supplementary angle, 135°, with the changed projection orientation. This is measured from the new references; the part remains in the inclined-face review set rather than being claimed as numerically identical. Within each final drawing, every measured value and reference survives reopening unchanged.

Sources, selection method, implementation decisions and reproduction commands are in [the reference review](drawing-reference-review-2026-10-26.md). Detailed measured results are in [the JSON summary](drawing-reference-validation-2026-10-26.json). The raw snapshots, FCStd documents, PDFs and PNGs are in `samples/validation/reference-final-2026-10-26/`.

Examples: [dimensioned plate](../samples/validation/reference-final-2026-10-26/part_0001.pdf), [two large standard views](../samples/validation/reference-final-2026-10-26/part_0013.pdf).

The dataset sheets are synthetic references. Equality of nominal measurements and passing layout checks do not prove complete feature recognition or a complete manufacturing specification.
