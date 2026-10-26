---
name: freecad-swiss-drawings
description: Create and review FreeCAD TechDraw drawings from STEP or other CAD models using the free-drawing tool and Swiss SN EN ISO TPD/GPS practice. Use for drawing generation, sections/details, inspection, export, and maintaining this drawing pipeline; not general-purpose solid modelling.
---

# FreeCAD Swiss drawings

Produce an editable drawing and review evidence while preserving the received model. Keep unknown manufacturing requirements explicit. This skill targets the `free-drawing-mcp` server and its FreeCAD addon; the complete standards are not bundled.

## Find the implementation

The known installation is `C:/Code/free-drawing`. Use the user's checkout when specified; otherwise verify this path or locate the repository. Read its AGENTS.md if present. Prefer the connected `free-drawing` MCP tools; call `drawing_status` before relying on an RPC session. If unavailable, use the repository CLI or `HeadlessBackend` with installed FreeCADCmd. Ordinary system Python usually cannot import FreeCAD's modules.

For a received model, read [the operating workflow](references/step-to-reviewed-drawing.md). For implementation changes or a failed geometry case, read [the engineering learnings](references/freecad-drawing-learnings.md). For standards decisions, read [Swiss TPD/GPS policy](references/16-swiss-tpd-gps.md) and the repository's `guidelines/sources/README.md`; verify editions when the project demands a current standard. Prefer newer maintained repository guidance if this packaged copy is older.

## Essential workflow

1. Preserve the input; use an explicit new output path. Record source revision and confirm units against a known dimension.
2. Collect available material, functional dimensions, fits/datums, tolerances, surface/edge requirements and approval context. Start a geometric draft without inventing missing values.
3. Generate with `create_drawing`, normally first-angle, metric, auto scale and automatic sections/details. These are house defaults; honour an explicit project specification. Keep status In preparation.
4. Inspect warnings, source/component count, section/detail plans and unplaced requirements. Review the rendered sheet, not just the report score.
5. Apply appropriate fixes and supplied specification data. A bounded autofix pass can address layout; review individual action results and inspect again. Regeneration does not replay manual edits.
6. Reopen the saved FCStd in a fresh process. Inspect the actual final PDF and provide source provenance, editable FCStd, PDF, preview, snapshot/report and unresolved items as appropriate.

## Decisions that prevent incorrect drawings

- A STEP B-rep supplies nominal geometry, not a complete manufacturing specification. Do not infer material, tolerance class, datum system, finish, thread class or edge allowance to obtain PASS.
- The current modelled-thread formatter still inserts `6H`/`6g`. Identify these as assumptions and obtain the intended class before release; do not describe them as extracted PMI.
- The current tool creates principal full/half sections and enlarged details. It does not automatically solve auxiliary, offset, broken-out or broken views, rib exemptions, or assembly selective sectioning. Preserve gaps as engineering work.
- Automatic sections of multi-solid models are deferred to preserve component identity. Half sections require demonstrated symmetry.
- Dimensions on hidden lines should be reassigned to actual visible cut/auxiliary geometry. Re-resolve the references; `EdgeN`/`VertexN` are not portable between views.
- Recompute dimensions before dedupe. Stale measurements can erase distinct requirements. Detail scales are absolute, and section/detail view centres differ from the original object's centre.
- Use the coordinate helpers: public page positions are bottom-left/Y-up; group items and dimension positions are relative; projected geometry is unscaled view-local. Manual cut coordinates refer to the aligned working model.
- `ISO 2768-mK` is a legacy specification requiring edition review. ISO 22081 needs actual values, scope and datum choices. Preserve TED/reference notation; do not strip brackets to satisfy a style check.
- A catalogue or preview verifies only what it contains. Label source scope and keep publisher PDFs out of redistributed skills. Do not claim complete SN EN ISO conformity from these examples.
- PASS can include REVIEW items. A high score or successful export is not release approval. Explicitly separate successful file generation, geometric checks, unresolved specifications and final engineering acceptance.

## Validation when changing the tool

Use `uv run --group dev pytest -q -m "not freecad"` for inspector changes and relevant real FreeCAD tests for view/geometry/persistence changes. Tests live in `tests/test_integration_freecad.py` and `tests/test_supplementary_freecad.py`. The latter exercises sections/details after reopening, both projection methods, hatch geometry, hidden dimensions and flange diameters.

For broader investigation, generate `samples/generate_complex_parts.py` inside FreeCADCmd and use `samples/run_samples.py --extra samples/complex --out <new-directory> --jobs 2`. Read its result JSON and individual findings; successful batch execution is not proof all drawings passed. Count distinct sources and avoid output-stem collisions. Preserve failing shapes as regression evidence and document unsupported cases without hiding them.
