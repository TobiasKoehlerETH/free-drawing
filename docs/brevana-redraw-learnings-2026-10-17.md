# Brevana customer drawing redraw: findings and improvement plan

This is a real-case evaluation of free-drawing, not a manufacturing release or a claim of complete SN EN ISO conformity. The case is a fictional workshop; the imported exterior model is a complex, previously edited vendor model. Evidence date: 17 October 2026.

## Result and scope

The redraw replaces the five PDFs and the Rev B drawing JPEG in `C:/Code/team-leader-workshop/challenge/participant/Brevana_Case/03_Customer_CAD/Drawings`. The reference book retains 43 pages: 38 part/assembly references and five original-component sections. Four separate sheets retain the Rev B drawing, Rev C drawing, B/C interface comparison and historical engineering change notice.

The delivery includes 47 FCStd files, their editable SVG sheets, inspection snapshots, source hashes and review images. These final sheets use a case-specific SVG adapter with source B-reps inside the FreeCAD documents. **They are not native associative TechDraw dimensions or views.** Editing the source geometry requires regenerating the SVG projections and sections. This is a documented fallback, not evidence that `create_drawing` completed this case automatically.

All five supplied STEP files are preserved. The original machine contains 48 solids; the 32 original-component reference entries cover all 48, including lettering grouped with its handwheel. M01 is the complete machine. R01-R04 and S01 are the revised group-head/mating-part references and current seal. Solution-only R05/S02 were excluded.

Existing dimensional requirements were preserved:

| Requirement, mm | Rev B | Rev C |
|---|---:|---:|
| Groove depth | 2.65 ±0.05 | 2.85 ±0.05 |
| Groove width | 4.80 ±0.10 | 5.40 ±0.10 |
| Locked mating-face gap | 0.10 ±0.08 | 0.30 ±0.10 |

The fresh section audit measures the gaps as 0.09999999998 and 0.29999999998 mm. Both groove-floor corners match the supplied nominal dimensions to less than 0.00001 mm. These are nominal CAD comparisons, not tolerance or physical performance validation. No material grade, finish, thread class, general tolerance class or approval was invented.

## Reproducible evidence

Environment: Windows; FreeCAD 1.1.1; free-drawing commit `db4c068d391009f18605d3bf49ce7e14aefeca51`. A headless status call succeeded. The full automatic pipeline was tried through `HeadlessBackend`; the fallback used FreeCAD's bundled Python and the same repository's `page`, `ops`, `render`, `snapshot` and `sections.cut_faces` modules.

All paths below are beneath `C:/Code/team-leader-workshop/challenge/generator/`:

| Evidence | Location |
|---|---|
| Source identities, object coverage and B-reps | `build/free-drawing-redraw/source_manifest.json`, `inventory.json`, `sources/` |
| Fresh component-preserving section polygons | `build/free-drawing-redraw/sections.json` |
| Numerical comparisons | `build/free-drawing-redraw/geometry_audit.json` |
| Successful native automatic pilot | `build/free-drawing-redraw/reference/P21.result.json`, `.FCStd`, `.snapshot.json`, `.inspection.txt` |
| Automatic dimensioned group-head timeout | `build/free-drawing-redraw/automatic-diagnostics/R01.result.json` |
| Stalled reference-case Python stack | `build/free-drawing-redraw/P01-stage-trace.txt` |
| Preserved interrupted/timeout job inputs | `build/free-drawing-redraw/diagnostic-jobs/`, `diagnostic-temp-jobs.json` |
| Final reference sheet construction records | `build/free-drawing-redraw/reference-final/*.creation.json` |
| Fresh-process reopen, raw inspection and Qt export | `build/free-drawing-redraw/{reference-final,controlled}/validation.json` and adjacent snapshots/reports |
| Qt PDFs before alternate final export | `build/free-drawing-redraw/qt-exports/` |
| Final PDF raster review | `build/free-drawing-redraw/review/` |
| Original delivered PDFs/JPEG before replacement | `build/free-drawing-redraw/original-delivery/` |

The case-local generators are `redo_customer_cad_extract.py`, `redo_customer_cad_projected.py`, `redo_customer_cad_sections.py`, `redo_customer_cad_validate.py`, `redo_customer_cad_pdf.py` and `redo_customer_cad_publish.py`. `redo_customer_cad_batch.py`, `redo_customer_cad_reference.py` and `trace_free_drawing_case.py` retain the automatic/native experiments. They are not general-purpose upstream APIs.

The final pipeline order is extraction → projection/section generation → fresh-process validation → searchable PDF export → raster review → publication. Running validation again deliberately regenerates the Qt PDFs, so run the final PDF exporter and review again afterward. The final PDF exporter uses the same saved SVG geometry; it does not recompute the model.

## Findings, priorities and acceptance criteria

### P1: bound expensive view construction and expose progress

The native P21 support-foot pilot completed creation, fresh-process reopen, inspection and export in 38.3 seconds. The dimensioned R01 group-head attempt exceeded the configured 600-second timeout (600.8 seconds observed). An undimensioned P01 reproduction also stalled; its Python trace was in `builder.create_drawing`, line 629, at `grp.addProjection(n)`. Automatic M01/P01 reference jobs were cancelled before creating deliverables. A separate direct native-view experiment also spent minutes recomputing M01, including a coarse-view attempt.

This establishes expensive view construction in these cases. It does **not** identify an OCC deadlock, prove that every native view fails, or isolate the effect of rotation from recomputation. The workstation was shared; the times are observations, not a controlled benchmark.

Using the received axes and `TechDraw.projectEx` with explicit view placement completed P01 in 10.8 seconds and M01 in 111.7 seconds. The first complete 38-reference pass totalled 222.6 seconds of per-item generation time. Curves were discretized at 0.025 mm model-space deflection for the SVG sheets; this is a rendering parameter, not an engineering tolerance.

Improve the API with explicit front/up directions, an option to preserve received orientation, per-stage timings, bounded construction and visible progress. Investigate batching recomputes and projection caching. Benchmark exact, polygonal and direct-projection paths on the same unchanged shape. Offer an explicit reference-only fallback with its associativity and approximation limits recorded.

Acceptance: include the group head, machine and simple foot as fixtures; distinguish creation, projection, snapshot and export timings; a timeout must return the stage, attempted direction and useful evidence. Do not silently substitute approximate geometry for controlling dimensions.

### P1: clean temporary jobs on timeouts

The R01 timed-out job left an `fd_job_*` directory. Two jobs terminated during the M01/P01 experiment also left directories. Their job JSON files are retained in the evidence folder. External termination is distinct from the library-managed timeout.

In `HeadlessBackend.run`, the `TimeoutExpired` branch raises before entering the later `try/finally` that removes the work directory. Put the whole subprocess/result lifecycle inside one cleanup boundary. Preserve an intentional diagnostic bundle on failure, and distinguish it from leaked temporary files.

Acceptance: a real short-timeout test proves that no unintended job directory or owned subprocess remains; the diagnostic error retains source, operation and duration. Never terminate unrelated FreeCAD sessions.

### P1: make drawing purpose an inspection input

The P21 pilot intentionally had no dimensions. Its ordinary inspection returned FAIL, score 83: missing units, three overall dimensions, tolerance scheme and material, plus a redundant-view warning. The actual `UNITS: mm` omission was corrected in subsequent work. Missing controlling dimensions/material/tolerances are expected for these explicitly undimensioned reference sheets; deleting their views to satisfy a manufacturing checklist would remove useful workshop evidence.

Final adapter sheets have no native `DrawViewPart` records: geometry lives in the SVG template. The current inspector cannot assess that geometry. Their raw FAIL reports were retained and were not converted to PASS. Numerical source checks, editable-field checks, page counts, source hashes and visual review provide separate evidence. A high score on a sparse snapshot would likewise be insufficient.

Add an explicit scope such as manufacturing, assembly, reference or change notice. Keep mandatory physical checks active, but classify intentionally omitted specifications appropriately. Add an unsupported-content finding when a page contains geometry the snapshot cannot represent. Do not infer scope merely from a title or waive failures for ordinary manufacturing drawings.

Acceptance: a reference sheet does not invite destructive deletion of useful views; a manufacturing sheet still requires controlling dimensions/specifications; an SVG-only sheet cannot receive a completeness claim from an empty native-view list.

### P1: validate editable template fields before export

A custom `<text freecad:editable="...">plain text</text>` template loaded without a useful editable-field map. FreeCAD returned an empty-key map, while the headless renderer replaced unknown editable fields with empty strings. The PDF contained geometry but no labels. This was caught by rendering the PDF. The working adapter uses unique `id` attributes and `<tspan>` children, matching the shipped template convention, and asserts that the expected number of editable fields was loaded.

The failure combined an adapter authoring error with silent acceptance by the pipeline. Template validation should report missing keys before saving or exporting. For an unknown key, either fail clearly or preserve the original literal text according to an explicit policy; do not erase it silently. Keep intentionally blank known title fields distinct from unrecognised fields.

Acceptance: test both custom-template forms, nonempty key names, missing fields, intentionally blank values and the rendered dimensional labels after reopen.

### P1: verify final export semantics and fonts

The Qt exports visually showed labels after the template correction, but `pypdf` extracted no text from the custom sheets. The offscreen rendering also substituted the displayed typeface. The delivered PDFs therefore use a small ReportLab exporter for the adapter's saved SVG vocabulary, with embedded Arial regular/bold fonts and searchable text. Qt PDFs remain as evidence. The final exporter rejects unsupported SVG elements rather than silently omitting them.

Do not assert equivalence between a preview, Qt schematic export and GUI TechDraw export. Test actual final PDFs for page size, glyphs, text extraction and dimensional strings. For this case the final path is **FreeCAD geometry → free-drawing section/page infrastructure → SVG adapter → ReportLab PDF**, not GUI TechDraw PDF.

Acceptance: tolerance signs, revision IDs and labels survive a fresh-process reopen/export; actual PDF text is searchable when promised; fonts are embedded or deliberately selected. Visually compare all supported backends using the same test sheet.

### P1: support component-preserving assembly sections

Automatic assembly sectioning is already deliberately deferred upstream. That is appropriate for this case: the head, mating portafilter and O-ring must retain separate identities. A free-state O-ring overlaps the mating part. A boolean union would erase distinctions, while a boolean interference result is not an elastomer deformation analysis.

The adapter calls `sections.cut_faces` separately for each globally placed component at Z = 103 mm. Opposite hatch directions distinguish adjacent metal parts. The ring stays separately outlined. Each face retains its outer and inner loops; even-odd hatching leaves cavities empty. Both revisions share the same view orientation and detail scale. Functional plane positions remain explicit.

Add per-component inclusion/exclusion, hatch angle and spacing, material identity and free-state/installed-state labels. Carry component identities through alignment. Provide datum/face references for a locked gap, not merely two nearby projected edges.

Acceptance: the B/C gap changes by 0.20 mm with placements applied; cavities remain unhatched; overlap does not fuse the ring into the portafilter; cut markers and view directions agree; selective-section decisions are explicit.

### P2: preserve clipping and hole topology across renderers

The repository's `_hatch_svg` implementation already documents that QtSvg has no `clipPath` support. The case adapter therefore clips polygons numerically and generates clipped hatch segments using even-odd intersections. It does not depend on SVG patterns or clipping masks. The first visual review also exposed a parent view extending into notes; the parent scale was reduced while retaining the separately stated section/detail scales.

Extract shared clipping/hatch utilities instead of reimplementing them in every adapter. Reserve annotation envelopes after font selection. Include view captions, leaders, cut markers and masks in layout checks. Avoid a crop boundary looking like an actual manufactured edge without a clear detail/crop label.

Acceptance: nested holes and disjoint cut regions remain open; adjacent-component hatches differ; captions and dimensions stay inside sheet bounds; a final PDF review agrees with the snapshot geometry.

The current S01 torus also produced an extra seam circle in the right projection although the front and right profiles should be identical. This was visible in the full-page review. The adapter checks equal X/Z extents and reuses the front profile for this known axisymmetric seal, following the earlier case generator's documented workaround. Keep this a narrowly identified torus case; equal extents alone are not a general proof of rotational symmetry. Add a real torus regression that distinguishes seams from manufactured edges in all principal directions.

### P2: use current component identity and global placement

The old sheet inventory referred to top-level object `ECM_Slim`. The current machine STEP was flattened during earlier branding cleanup and no longer had that object. The redraw reconstructs M01 from the current 48 placed leaf components and uses the older inventory only for sheet names/coverage. It does not reuse stale cached projected geometry.

The Rev C mating part has a 0.20 mm global placement change. Copying local shapes without their full placements would silently change the locked gap. The source manifest and component-coverage audit make this check repeatable.

Final provenance review also caught the adapter initially embedding the current Rev C ring in the Rev B FCStd while its PDF section correctly came from Rev B. The delivery now embeds separate rings extracted from each actual revision. Fresh-process checks verify their top-face heights against the corresponding groove floor. Check the embedded editable source as well as the PDF: a visually correct export can conceal a wrong-revision source object.

Improve object selection with explicit source object/label records and ambiguity detection. Preserve assembly paths and transforms. Exporting an isolated BREP loses STEP product names; keep a sidecar mapping when that intermediate is used. Do not deduplicate different revisions by nominal dimensions or filename stem alone.

Acceptance: hierarchical and flattened variants produce the same placed geometry and complete component coverage; missing/ambiguous objects fail with a useful message; a source hash change invalidates cached projections.

### P2: preserve authoring intent and document provenance

This task is organizer authoring, not a participant attempt at the workshop. The existing drawings deliberately reserve dimensions for the sealing interface. Adding automatic dimensions to every exterior part would alter the exercise. The engineering change notice's historical approval text is retained as fictional case history; the redraw itself remains In preparation. The regenerated Rev B JPEG is a rendition of the redrawn sheet, not a newly discovered historical scan.

Keep original delivery backups outside the participant handout. Package editable sheets with their source geometry and explicit rebuild instructions. Separate release approval, file-generation success, numerical CAD checks, visual acceptance and raw inspection results. Do not refresh unrelated decks, handout ZIPs or solution documents as a hidden side effect of a drawing run.

## Recommended upstream work order

1. Add stage telemetry and guaranteed timeout cleanup; preserve a minimal reproducible diagnostic bundle.
2. Validate template field maps and exported dimensional text; prevent silent label loss.
3. Introduce drawing scope and unsupported-content inspection, without weakening manufacturing checks.
4. Add explicit orientation and a bounded reference-drawing mode with recorded backend/accuracy limits.
5. Add component-aware section plans and functional face-to-face gap requirements.
6. Consolidate hatch/clipping/layout utilities and add the relevant PDF, persistence and assembly-placement regressions.

No free-drawing production code was changed during this redraw. The case-local adapters are a concrete workaround and a source of regression scenarios. They should not be promoted into the general generator without focused tests for geometry, persistence, export and inspection scope. The engineering owner must still supply and approve any manufacturing specification.
