# FreeCAD drawing implementation learnings

Evidence date: 2026-10-17. Environment: Windows, FreeCAD 1.1, repository `free-drawing`. These observations describe this implementation; they are not universal limitations of FreeCAD or normative standard clauses.

## What changed

- `sections.py` already implemented cuts/details, snapshots and rendering, but `builder.create_drawing` never called it. The builder now runs the post-pass after primary layout and records reasons, view names, creation notes and no-fit outcomes.
- Re-plan against the original views plus the actual generated section/detail geometry. Preserve coverage before replacing the original dimension set. A section without relocated dimensions can leave the original hidden-line problem unresolved.
- Recompute before dimension deduplication. In the flange test, newly created dimensions had stale measured values and the dedupe pass removed distinct diameters. The rerun retains the 90 mm pitch circle and 120 mm outside diameter.
- Detail preference moves suitable dimensions into an enlarged representation. It does not guarantee that every small feature is detected or that the final annotation layout is collision-free.
- No-fit sections/details are removed and reported, rather than placed at the page centre. Failed creation and missing hatch geometry are inspection failures.
- Automatic half-section selection now requires a conservative check for coaxial cylindrical symmetry. A general classification such as `turned` is insufficient proof of symmetry.
- Automatic multi-solid sections are deferred for explicit component exclusions and adjacent-part hatch treatment. Fusing an assembly for a cut can destroy the distinctions the drawing needs.
- Inclined cylinders and recognised freeform surfaces record definition gaps. Overall dimensions alone must not imply a complete definition of them.
- Defaults no longer invent `ISO 2768-mK`, `Ra 3.2` or a universal edge break. Missing requirements remain open. The inspector no longer auto-strips parentheses or creates edge allowances.

## Coordinate and geometry invariants

| Data | Frame / interpretation |
|---|---|
| Public action positions | Page mm, bottom-left origin, Y up, unless the action explicitly describes a local field |
| TechDraw projection items | Their X/Y offsets are relative to the projection group |
| View edges and vertices | View-local, unscaled model coordinates |
| Dimension geometry returned by TechDraw | May use scaled view coordinates with Y down; use the repository conversion helpers |
| Section normal and origin | Aligned model coordinates; normal is not the in-plane cutting-line direction |
| Detail anchor/radius in the internal planner | Parent-local model coordinates; detail scale is absolute, not a multiplier |
| View centre | Sections and cropped details require their own source-to-view mapping; do not assume the original solid centre |

Resolve geometric references again when moving a dimension between views. Edge/vertex indices are local to that view and can change after recompute. Use helpers in `geometry.py`, `sections.view_center3d` and `dimensions.ViewGeom`. Do not copy an `EdgeN` from the parent to the child and assume it identifies the same feature.

## Tested corpus and interpretation

The broad run produced 36 distinct drawings: 28 existing samples and 8 new stress shapes. The original batch log contains 44 rows because case-insensitive Windows globbing selected the extra STEP files twice; count unique source parts, not rows. The runner now filters suffixes case-insensitively in one pass and deduplicates resolved paths.

The broad run generated 14 sections and 20 details across those unique cases. It ran while fixes were being developed, so its summary is diagnostic evidence, not a frozen final-version qualification. Targeted reruns and the regression suite verify affected paths after fixes. See [validation evidence](validation-2026-10-17.md).

Representative outcomes:

| Case | Observed result / lesson |
|---|---|
| Counterbored block | Section creates material-only hatch and resolves the hidden depth dimension onto visible section geometry |
| Tube | Hollow coaxial geometry supports a half section; centre line and annular material need visual checking |
| Flange | Recompute before dedupe preserves outside and pitch diameters |
| Micro grooves | Enlarged detail receives controlling diameter and baseline dimensions; parent references remain visible |
| Rotated manifold | Alignment handles the transformed model, but existing annotation crowding still needs correction |
| Oblique bore | Principal views can hide missing bore definition; explicitly flag auxiliary-view/diameter/location work |
| Lofted adapter | Unsupported profile/overall definition remains visible in E.1 rather than a misleading completeness claim |
| Ribbed bracket | Inclined face dimensioning remains incomplete; longitudinal rib section convention needs deliberate treatment |
| Assembly | Preserve part identity and review selective sectioning; no automatic fused section |
| Threads / sheet metal | Some thread extents, symbols, inclined angles and annotation collisions remain unresolved in the broad corpus |

All broad-run drawings report missing material/tolerance input because the corpus deliberately supplies no manufacturing specification. These expected failures do not erase real geometry/layout failures. Report both categories explicitly.

The final suite passed 134 tests (127 pure Python, 7 real FreeCAD). The five-case final stress rerun completed and retained the documented unresolved findings. Passing the regression suite establishes those tested behaviours, not release readiness for every shape.

The 2026-10-17 source review found that `threads.callout` supplied `6H` / `6g` without design input. This was removed on 2026-10-29. Generated callouts now omit an unspecified class and inspection reports it as incomplete. Existing drawings containing automatically assigned classes still require review.

## Standards and source handling

Use the Swiss SN EN ISO adoption and the contracted edition. Public catalogues verified ISO 128-3:2022, ISO 22081:2021, ISO 8015:2011 and ISO 21920-1:2021. Normen-Auszug 2026 is a teaching/practice compendium, not itself a complete machine-readable specification.

The old guides mixed obsolete editions, house defaults and ISO requirements. The new Swiss guide takes precedence. ISO 2768-2 class K is not mechanically convertible into ISO 22081. Do not import ASME RFS/envelope assumptions into ISO GPS. A geometric bounding-box origin is not a functional datum selection. Boxed TEDs and parenthesized reference dimensions carry meaning that cleanup must preserve.

Six public PDFs were downloaded and validated as PDFs with page counts and SHA-256 hashes. Two EVS download URLs initially returned HTML with a `.pdf` filename; checking only the HTTP success or suffix would have falsely reported success. Verified iTeh preview URLs replaced those responses. The SNV GPS reference has a 2026 heading but a table referring to 2022 pages. Record this discrepancy rather than silently relabelling citations.

The PDFs are locally available and excluded from Git; their copyrights are not covered by the repository licence. The source manifest and links make them discoverable without redistributing them in the agent skill. Missing normative clauses remain unverified.

## Regression and operational practice

Run pure inspection tests for policy/metadata changes and real FreeCAD tests for geometry, persistence and export changes. A useful real test creates a drawing, reopens the saved file in a second process, verifies geometry/hatching/parent references, and checks measured values and inspection findings. Testing only a mocked builder would have missed the original disconnected section engine.

Preserve input and output files separately. `.FCStd` source handling can load an existing document: supply a separate output path. Headless processes do not preserve in-memory state between calls; persist changes before calling the next operation. `create_drawing` rebuilds a draft, so record manual edits before regenerating. Review every action result; the operation container succeeding does not prove that each action succeeded.

The CLI has fewer options than the MCP API. A CLI exit code of 1 is also used for inspection FAIL after successful generation. `samples/run_samples.py` catches errors per part and its process exit alone is not a qualification result: read `ok`, findings and artifacts. Avoid simultaneous jobs writing the same output stem; use distinct output directories, and rename colliding input stems before batch processing.

## Brevana customer-case follow-up

The [Brevana redraw evaluation](brevana-redraw-learnings-2026-10-17.md) records a 47-sheet case with a 48-component machine, preserved Rev B/C sealing requirements, an automatic group-head timeout, projection-stage traces, temporary-job cleanup gaps, template label loss, export/font issues and reference-scope inspection limitations. It includes concrete workarounds, evidence paths and acceptance criteria. Its SVG-based deliverables are explicitly non-associative; they are not an additional successful qualification of the automatic dimensioning pipeline.

## Remaining work

1. Layout convergence after dimension relocation: annotation/view envelopes must be reassessed together, including detail boundaries and captions.
2. Arbitrary auxiliary, offset, broken-out and broken views; explicit rib/component exemptions with per-component hatching.
3. Feature identity for equal-radius arcs, complex hole stacks and repeated instances; avoid relying solely on matching nominal sizes.
4. Functional datum and GPS specification authoring from user-provided intent, plus structured inspection of those requirements.
5. Thread extent/symbol checks, complex sheet-metal angle coverage and explicit manufacturing-state handling.
6. Additional test families: nested cavities, intersecting passages at non-principal angles, curved ribs, dissimilar materials, splines, cones and varied export backends. The current 36-part corpus is broad but not exhaustive.
