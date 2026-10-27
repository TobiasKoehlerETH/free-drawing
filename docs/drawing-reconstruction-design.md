# Drawing-only reconstruction and verification

The reconstruction worker receives only published drawing evidence. It never opens the drawing FCStd (which contains the source solid), the input STEP, or the generator's feature catalogue. The source is opened separately for geometric comparison and visible drafting repairs.

## Public operation

`verify_definition(backend, drawing, source, output_dir, max_repairs=2)` exports evidence from a reopened drawing, rebuilds in a dedicated FreeCAD worker, compares the independently exported STEP against the input in another process, and applies at most two visible repairs. Each attempt has its own artifacts. Missing dimensions, contradictory dimensions, ambiguous depths, unsupported shapes and geometric mismatches remain distinct outcomes.

## Boundaries

- `drawing_evidence.py` exports an allowlisted page description: drawn edges/circles, projection relationships, displayed dimension text/witnesses and visible notes. It separately writes the comparator's orientation frame.
- `drawing_definition.py` interprets printed constraints. Drawing coordinates associate witnesses and entities; they never supply missing dimensional values. Every solved parameter records the dimensions or notes supporting it.
- `rebuild_worker.py` accepts only an evidence path, output directory and fixed numerical tolerance, then constructs actual solids from the solved plan. No source-bearing operations dispatcher is imported.
- `paper_geometry.py` captures the drawn curves, including enabled hidden edges, without source-solid access. `projection_check.py` reprojects the independent model and checks every view, including curve topology and dimension-scale consistency.
- `definition_compare.py` compares solids with fixed numeric tolerances and proper rigid placement only. It checks validity, solid count, extents and bidirectional Boolean differences; equal volume alone cannot pass.
- `definition.py` owns the bounded feedback loop and stops on unchanged evidence, unsupported scope, exhausted repairs or errors. Repairs must publish facts as native dimensions or visible notes before another reconstruction.

## Scope and honesty

The working grammar covers constant-depth rectangular and circular profiles, cylindrical holes and counterbores, rectangular openings, capsule slots, and dimensioned axisymmetric line profiles with steps, grooves and 45° chamfers. Other geometry is audited but cannot receive a successful reconstruction verdict until its constructor and constraints exist. A match is qualified by this grammar and a stated numerical comparison tolerance. It does not prove uniqueness among all imaginable solids or manufacture missing tolerance/material requirements.

Every interpreted control must bind to drawn geometry and participate in the solved constraints. Disconnected controls, unsupported text or local notes, unhandled views and unsupported curves prevent success. Small paper-coordinate differences do not become implicit equality constraints. The worker reprojects its own solid against every defining view before comparison with the original STEP. Boolean differences, volume, bounds and sampled edge/face deviations are checked separately; the samples are diagnostic numerical evidence, not a mathematical maximum-distance certificate.

Grouped through-opening notes are published only after all openings covered by the request pass the source-aware void check. With multiple views, the source must also match a uniform extrusion before a global opening-depth note is allowed. A failed group cannot leave behind a partial false note. Thickness repairs create a native dimension on an edge view, adding the view if necessary; they never publish a `t12` substitute. Repairs that cannot be placed without collisions are rolled back. The input STEP is hashed and remains unchanged.

The rebuilder consumes native drawing evidence, not arbitrary scanned PDFs. Displayed text is authoritative; source extents, raw dimension values, source feature recipes and B-reps are excluded. A separate source-frame STEP is produced by rigid placement of the independent rebuild for overlay review. STEP text/header identity and the original feature tree are not required to match.

The initial implementation does not interpret arbitrary local notes, GD&T, general blends, or section/detail cutting-plane correspondence. Those inputs stop with an explicit unsupported result. Supported numeric comparison tolerances range from 0.00001 to 0.01 mm, with 0.00001 mm as the default. View scales below 1:100 are rejected by the reconstruction precision boundary. This process/API separation is not an adversarial operating-system sandbox.

## Synthesis

Two independent designs converged on an isolated drawing interpreter. Candidate A is the base (9/10 against the design rubric), with candidate B's explicit grammar scope, parameter provenance and evidence-hash freshness checks. The independent reviewer agreed. Visual-hull reconstruction, source feature-recipe replay and source-guided dimensional fitting were rejected because they can conceal missing dimensions.

Verification includes real rebuilt solids, removal/change of printed dimensions, equal-volume but different geometry, forbidden source fields, and a missing-dimension repair followed by a successful second reconstruction. Existing twelve-part and six external-part drawings are all audited; unsupported results must not be counted as successful round trips.

## Generator changes from the audit

Plates retain a dimensioned edge view at the same scale as the main view. Sheet-metal wall thickness is dimensioned between the wall faces, with a section when those faces are hidden. The inspector no longer accepts a thickness note as a substitute for a view dimension or proposes such a note as an automatic fix.

Reprojection uses the independently exported STEP after reopening it. This restores the tangent-edge continuity used by TechDraw and checks the same solid that is passed to the comparator. Slot seams must not create false extra edges simply because the independent solid was built in memory.

Hole stacks retain their full axial extent, including conical end treatments. A bore that opens into an internal passage remains blind unless its end reaches an exterior opening. Disconnected coaxial blind holes remain separate. Matching countersinks on both ends receive `BOTH SIDES`; external chamfers do not become countersinks.

The inspector matches circle axes across standard views in the same projection group before reporting missing multiplicity. Equal diameter alone cannot merge different depths or specifications. This association is inspection metadata and is excluded from drawing-only reconstruction evidence.

Angular dimensions now export measured arcs, arrows and witness extensions. Placement searches both vertically opposite angle sectors and keeps labels beside their own arcs. Collision checks use the full arc and radial leader, including nearly overlapping strokes. Moving a label does not change its geometric references or measured angle; a real FreeCAD test checks that invariant after reopening.

The final corpus results and remaining work are recorded in [the reconstruction validation](drawing-reconstruction-validation-2026-10-27.md).
