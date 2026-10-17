# Technical Drawing Guidelines

**New drawings follow [Swiss SN EN ISO TPD/GPS practice](16-swiss-tpd-gps.md).** This policy takes precedence over historical examples below. Public Normen-Auszug 2026 material and standards previews are indexed in the [source register](sources/README.md). A check score is not a standards-conformance certificate.

This folder is the working reference for `auto-drawing`. It is organized as a compact, practical knowledge base: short rule sheets, local pictures, and example callouts that are easy to reuse while generating or verifying drawings.

The emphasis is now:

- quick interpretation of common drawing notation
- practical defaults that affect quoting and manufacturability
- visual examples for recurring callout types
- self-contained markdown with local images only

## Current Automation Status

The repo implements a Python MCP server and a FreeCAD TechDraw addon with headless and GUI backends.

Current behavior:

- accepts STEP / STP input
- creates a FreeCAD drawing, a JSON inspection snapshot and a rendered preview
- keeps A3 / first-angle / metric as the default sheet standard
- uses line-based orthographic views and a compact isometric recognition view
- generates full/half sections and enlarged details where the supported geometry calls for them
- writes inspection findings as `pass`, `warn`, `review`, `fail` or `na`

## Scope

Primary convention: ISO / DIN style technical drawings, metric units, first-angle projection by default, ISO 8015 independency principle, ISO 2768 general tolerances, and common European notation.

The guides also explain common cross-market variants when they are useful to recognize on incoming drawings:

- first-angle vs third-angle projection
- European vs US text orientation for dimensions
- ISO vs Unified / pipe thread notation
- ISO vs ASME-style GD&T reading patterns

## Project Overrides

For the current `auto-drawing` pipeline, also enforce these project-specific checks:

- Use `MMGS` document units and show dimensions in `mm`.
- Keep the sheet background white.
- Keep orthographic views at `1:1` unless they do not fit on A3.
- Use hidden lines visible for orthographic views.
- Keep the isometric recognition view compact at `1:2` in the bottom-right zone.
- Use shaded with edges for the isometric view only; do not shade the orthographic views.
- Treat vertical and horizontal ordinate dimensions as the top-priority dimensioning method wherever the geometry allows.
- Avoid overlapping dimension lines, leaders, and value text.
- Use boxed theoretically exact dimensions and clearly non-controlling reference dimensions when required by the specification.
- Do not let a controlling requirement appear twice on the sheet.
- Avoid text/model overlap with the title block.
- Avoid duplicate dimension text or dual-unit display.
- Run a duplicate-dimension dedupe pass before accepting the sheet.
- Recreate the drawing from scratch on each verification pass so the exported preview reflects the current automation, not stale dimensions.

## Folder map

| # | File | Focus |
|---|------|-------|
| 00 | [00-sheet-and-title-block.md](00-sheet-and-title-block.md) | Title block fields, sheet metadata, defaults |
| 01 | [01-lines-and-weights.md](01-lines-and-weights.md) | Line hierarchy and weights |
| 02 | [02-views-and-projection.md](02-views-and-projection.md) | Orthographic views, first-angle vs third-angle |
| 03 | [03-section-detail-broken-auxiliary.md](03-section-detail-broken-auxiliary.md) | Sections, details, broken and auxiliary views |
| 04 | [04-dimensioning.md](04-dimensioning.md) | Dimension methods, symbols, radii, worked examples |
| 05 | [05-tolerances-general-and-fits.md](05-tolerances-general-and-fits.md) | IT grades, fits, tolerance styles, ISO 2768 |
| 06 | [06-gdt.md](06-gdt.md) | Feature control frames, datums, modifiers, risk cues |
| 07 | [07-surface-texture.md](07-surface-texture.md) | Surface finish symbols, lay, N-grades, process ranges |
| 08 | [08-welding.md](08-welding.md) | Welding symbols and callouts |
| 09 | [09-machining-threads.md](09-machining-threads.md) | Thread families, fit classes, handedness, examples |
| 10 | [10-material-and-heat-treatment.md](10-material-and-heat-treatment.md) | Material, heat treatment, coating and process notes |
| 11 | [11-revisions.md](11-revisions.md) | Revisions and change marking |
| 12 | [12-bom.md](12-bom.md) | BOM / parts-list conventions |
| 13 | [13-symbols-and-notation.md](13-symbols-and-notation.md) | Fast symbol reference and notation habits |
| 14 | [14-edges-and-notes.md](14-edges-and-notes.md) | Edge callouts, deburr rules, global vs local notes |
| 15 | [15-sheet-metal-bends.md](15-sheet-metal-bends.md) | Bend callouts, reliefs, bend math, formed features |
| 16 | [16-swiss-tpd-gps.md](16-swiss-tpd-gps.md) | Swiss policy, current standards, design-input boundaries and complex-part review |
| - | [CHECKLIST.md](CHECKLIST.md) | Flattened verification rubric |
| - | [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) | Audit output from the verification pass |

## Images

Local diagrams live in [images](images). They are intentionally simple and schematic so the docs remain portable and render without external links.

## How to use these files

1. Start with the numbered topic that matches the callout being generated or checked.
2. Reuse the compact examples directly when annotating drawings.
3. Use [CHECKLIST.md](CHECKLIST.md) for binary visual verification.
4. Prefer local topic cross-links over web references.

## Editing rules

- Keep the files compact and example-heavy.
- Prefer one short table plus one or two worked examples over long prose.
- Keep images local to this folder.
- Record primary-source URLs, editions and verification scope in the source register.
