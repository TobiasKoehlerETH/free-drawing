# Offline drawing evaluation — 18 October 2026

This evaluation tests whether local deterministic drawing engines can produce useful drawings without an MCP server or per-part agent decisions.

The deterministic engine creates useful drafts, but complex parts still need review. One-shot MCP produces the same drawing as the offline engine. Agent review improves layout; it does not supply missing manufacturing intent.

Contents: [Protocol and evidence](#protocol-and-evidence) · [Broad benchmark results](#broad-benchmark-results) · [Silicone-cap result](#silicone-cap-result) · [Changes made for the requested drawing policy](#changes-made-for-the-requested-drawing-policy) · [Offline and MCP comparison](#offline-and-mcp-comparison) · [Evaluation limits](#evaluation-limits).

## Protocol and evidence

- Engine: FreeCAD 1.1.1, copied into `.scratch/offline-engine-2026-10-18` before the final run. Each result manifest records code/resource hashes before and after execution.
- Inputs: 28 original samples, eight existing complex parts, ten additional stress parts, and the user's silicone-cap model. Inputs are hashed and preserved.
- Broad-run settings: A3, first-angle projection, automatic scale, at least Front/Top/Right, automatic sections/details, and an isometric view. This reflects the earlier requested three-view policy; the user subsequently restored adaptive view selection.
- Every input follows the same one-shot creation path. No material, tolerance, datum, or finish is invented. No corrective pass runs in the offline baseline.
- A fresh FreeCAD process reopens each saved drawing, exports a schematic PDF and PNG, and records a second snapshot. This tests the headless export path, not GUI TechDraw export.
- The agent reviews results after creation. Its observations do not feed back into the offline baseline.

The repeatable entry point is `samples/benchmark_offline.py`. Per-case files, manifests, and summaries are under `samples/validation-2026-10-18/`. These local artifacts are excluded from Git.

## Broad benchmark results

| Outcome | Result |
|---|---:|
| Unique inputs | 47 |
| Completed creation, fresh-process reopening, PDF and PNG | 46/47 |
| Completed cases with no non-specification inspector failures | 24/46 |
| Completed cases with additional definition/layout failures | 22/46 |
| Completed drawings preserving measurements and canonical snapshot data | 45/46 |
| Generated sections / enlarged details | 18 / 25 |
| Generated dimensions in completed cases | 437 |
| Median observed creation time | 25.2 seconds |

The 24 cases without additional inspector failures are not certified drawings. They still have review items, may have warnings, and include false negatives identified below. Every completed case has missing material/tolerance findings because those inputs were deliberately unspecified.

`stress_splined_coupling` reached a saved FCStd after about 160 seconds, then exceeded the 240-second create-and-snapshot limit. It did not complete inspection or export. The partial file is retained as diagnostic evidence.

`tiny_pin` exposes a correctness failure after saving. Its hole-location dimensions change from **4 to 0 mm** and **2.5 to 0 mm** on reopening. Both reference the same detail-view vertex. The inspector still reports its overall and circular dimensions as complete. This invalidates that drawing despite a successful export.

Two strict snapshot comparisons also differ for `flange` and `stepped_housing`. Independent checks show only reversed sampling order around complete cosmetic circles. Their point sets, centres, radii, styles, measurements, and references remain equivalent; these are not drawing changes.

The largest reported categories are missing definition (47 findings), title-block/frame interference (17), and dimension collisions (nine). Visual sampling also found detached radius leaders that those checks miss. The curved tube lacks its known 35 mm bend-centre radius; freeform lofts and skew passages remain incompletely defined.

Full per-part counts are in `samples/validation-2026-10-18/per-part-results.md`. Persistence classifications are in `samples/validation-2026-10-18/persistence-analysis/summary.json`. The independent checks do not alter the original result logs.

## Silicone-cap result

This section describes the original fixed-policy output. The final comparison below uses the latest dimensioned-only and cut-face-only settings.

Source: `C:/Code/CFS-LF/Hardware/15_SM_U-TEK_Stacked/CAD/Assembly/silicone_cap_board_outline.step`.

SHA-256: `f88055992c22f7435b9ec049647385ef2bad254237d4b6537abd47325206b25b`.

Independent source inspection found one valid solid with 44 faces: 13 planes, 16 cylinders, seven cones, and eight B-spline surfaces. Its envelope is 39.401 × 37.801 × 6.1 mm.

The offline run produced three principal views, one full section, three details, one isometric view, and 18 dimensions. Creation took 62.4 seconds; reopening and exporting brought the total to 91.6 seconds. A repeat took 74.8 seconds for creation and produced the same semantic drawing fingerprint. Concurrent machine load means these times are observations, not isolated performance measurements.

The inspector reports six failures, one warning, and nine review items. Two failures concern intentionally unspecified material and tolerances. Four concern geometry definition or centrelines:

| Finding | Consequence |
|---|---|
| Central ⌀20 circle has no size dimension | The central cap diameter is not defined on the sheet. This is a material disk, not a drilled hole. |
| One circle lacks a centre mark | The axis is incompletely communicated. |
| Arc-centre X location is unplaced | The automated dimension plan is incomplete. |
| Freeform surfaces need a controlled model/profile specification | Analytic dimensions alone cannot define the eight curved transition faces. |
| Three radius dimensions reference hidden geometry | Their controlling features remain difficult to read. |

Visual review of the actual PDF reveals another defect: R15, R16, and R16.7 leaders float in empty sheet space. They belong to the enlarged detail but do not meet its visible arcs. The built-in score of 85/100 does not identify this defect.

Section A–A cuts the cavity through its centre. At 1:1, the 0.7 mm contour lines merge across 0.7 mm walls. Its chosen view axes also display model Z downward. These choices make the section difficult to interpret; successful hatch generation does not establish readable presentation.

Independent nominal geometry also identifies a 1.5 mm central material thickness, shoulder levels of 4.9 and 5.4 mm, and shallow roof tapers. A 6.1 mm overall height and 4.6 mm underside level imply central thickness, but the raw drawing does not directly call it out. It does not fully define the shoulder and taper geometry.

## Changes made for the requested drawing policy

The earlier benchmark retained Front, Top, and Right. After the user withdrew that requirement, the working engine returned to automatic selection of the smallest sufficient view set. Explicit view-list overrides remain available. Results from the earlier fixed-policy run are not relabelled as tests of the revised policy.

The centreline export defect was traced to cosmetic edges being included among model outlines. Geometry enumeration now excludes those edges, and snapshots preserve their line style, weight, and circular or curved shape. Exports show dash-dot axes and pitch circles. Dimension extension lines remain solid.

The frozen engine passed 139 pure-Python tests and eight targeted real FreeCAD tests. The latter include a fresh-process regression for dash-dot hole axes and pitch circles, alongside section/detail persistence and PDF/PNG export.

Two optional creation settings implement the final requests:

- `dimensioned_only=True` hides every view without an attached dimension. Native projection anchors and parents remain in the document, preserving dependencies. Undimensioned isometric views are omitted from the sheet.
- `section_cut_only=True` creates full sections from exact planar material faces. The silicone-cap section has 22 actual projected edges rather than 28, with no background edge across the cavity and no hidden edges. The native hatch and remapped dimensions survive reopening. Existing full sections can be converted or restored through `set_view(cut_only=...)`.

Four new real FreeCAD tests cover the dimensioned-only policy, including a hidden Front anchor. Another covers cut-face conversion, reopening, native hatch, PDF export, restoration, and direct creation. All five passed. The inspector also honors the explicit omission of an isometric view; its targeted tests passed.

The final pure-Python suite passed **140 tests**. Compilation and whitespace checks passed. The source STEP hash remained unchanged after all runs.

## Offline and MCP comparison

All final drawings use the same source hash and frozen CAD engine in `.scratch/offline-engine-adaptive-2026-10-18`. Both one-shot paths request automatic views, dimensioned-only output, cut-face-only sections, and the same A3/first-angle/auto-scale settings. The part still uses three principal views because each carries dimensions; there is no minimum view-count rule.

MCP was exercised through the real local stdio JSON-RPC protocol, with initialization, tool discovery, and recorded `tools/call` messages. It used the headless backend, not a GUI session. `layout_autofix=False` isolates one-shot generation. The MCP default normally enables a deterministic layout-fix pass; that is a workflow difference, not a different drawing engine.

| Workflow | Dimensions | Section | Extra definition/centreline failures | Visual result |
|---|---:|---|---:|---|
| Offline, one shot | 18 | Cut faces, 1:1 | 4 | Small section; three detached radius leaders |
| MCP, one shot | 18 | Cut faces, 1:1 | 4 | Semantically identical to offline |
| MCP with automatic fixes and agent review | 19 | Cut faces, 5:1 | 3 | Added ⌀20; clear cavity; radius leaders meet their arcs |

All three retain two additional missing-specification failures for material and tolerances, one warning concerning dimensions on hidden geometry, and nine review items. The assisted result remains a draft. Its remaining definition findings concern the missing central centre mark, arc-centre location, and freeform specification.

The agent made nine explicit layout actions: enlarge and move the section, move a detail, and relocate six dimension labels. Existing measured values were preserved. The new nominal ⌀20 dimension comes from an automatic geometry-backed correction. No material, tolerance, datum, or finish was fabricated.

A separate offline fixed-rule correction pass produced exactly the same snapshot as MCP's automatic correction pass. The offline engine therefore does not need MCP or agent reasoning for those corrections. The agent's added value in this case is visual diagnosis and choosing a readable arrangement.

The two one-shot outputs have identical canonical geometry, dimensions, annotations, and layout. Final independent inspection uses the same policy-aware checker for all three. That checker marks the intentionally omitted isometric view as not applicable. Raw protocol reports are preserved; `comparison-inspection-provenance.json` records the final checker and input hashes.

Final artifacts:

- Offline: `samples/validation-2026-10-18/adaptive-offline/`
- One-shot MCP: `samples/validation-2026-10-18/adaptive-mcp/`
- Reviewed MCP drawing: `samples/validation-2026-10-18/adaptive-assisted/`
- Verification: `comparison-verification.json`, `comparison-final.json`, and the per-workflow protocol logs under the same evaluation directory.

## Evaluation limits

Successful generation, saved-document integrity, nominal feature coverage, visual readability, and manufacturing specification are separate outcomes. Missing material and tolerance inputs are recorded separately from drawing defects. A high inspection score is not an acceptance percentage.

The corpus is broad synthetic coverage plus one user model. It does not establish reliability for every CAD model, complete standards conformity, or manufacturing release readiness. The 47-input run used the earlier fixed three-view policy. The final revised policy was verified on the silicone-cap comparison and dedicated regression cases, not rerun across all 47 sources.

The next engineering priorities are persistent detail-vertex identity, detection of detached leaders, automatic readable scales for thin sections, and coverage of oblique/freeform geometry. The timeout case also needs profiling before the engine can promise bounded unattended processing of arbitrary complex parts.
