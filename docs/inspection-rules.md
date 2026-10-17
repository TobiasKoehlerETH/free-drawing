# Inspection rules

The inspector (`src/free_drawing_mcp/rules/`) checks a drawing against every item of [guidelines/CHECKLIST.md](../guidelines/CHECKLIST.md). It runs in pure Python on the JSON snapshot taken from FreeCAD (`get_drawing_snapshot`). No FreeCAD is needed for the check itself, and a full A-P pass takes about 15-20 ms.

## How findings work

Every checklist item yields at least one finding, so a report always shows full coverage.

| Status | Meaning |
|---|---|
| `fail` | The rule is violated. Counts against the score. |
| `warn` | Probably wrong, or a style issue. Counts half. |
| `review` | Requires visual or engineering judgment, including specification intent. Not scored. |
| `pass` | Checked and fine. |
| `na` | Not applicable to this sheet (for example, no sections, no GD&T, no threads). |

- **RESULT** is `FAIL` if any check fails, `WARN` if any warns, otherwise `PASS`.
- **Score** = `100 × (pass + 0.5·warn) / (pass + warn + fail)`.
- A finding may carry a **fix**: a list of action dicts for `apply_drawing_actions`.
  - **auto** fixes are safe without human input. `autofix_drawing` applies them all and re-inspects, up to `max_passes`.
  - **needs input** fixes contain a `<placeholder>` (for example a material) that the agent must replace with a real value.
- Findings with a `bbox` are boxed on `render_drawing`: red for fail, orange for warn.

"Automated" in the tables below means the decision comes from the snapshot data. "Visual REVIEW" means the rule reports `review` and gives a hint.

## A. Sheet & title block ([guideline 00](../guidelines/00-sheet-and-title-block.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| A.1 | Sheet size matches declared size | Automated: template name starts with the detected ISO size | - |
| A.2 | Border and filing margin | Automated: drawing frame from the template, ≥ 20 mm on the left and ≥ 10 mm elsewhere (WARN otherwise) | Hint: use an ISO 5457 template |
| A.3 | Centring marks | Automated: centring marks found in the template SVG (WARN otherwise) | - |
| A.4 | Title block bottom-right | Automated: title-block box touches the right and bottom edges of the frame | - |
| A.5 | Units, scale, material, revision, sheet number | Automated, three checks: (1) title, scale, revision and sheet-number fields are not empty or template placeholders; (2) units are declared in the notes (`UNITS: mm`); (3) the title-block scale equals the view scale. Material is checked in K.1. | (1) `set_title_block` with placeholders (needs input); (2) append `UNITS: mm` to the notes (**auto**); (3) `set_title_block` with the correct scale (**auto**) |
| A.6 | Tolerance scheme | Detects ISO 2768 / 22081; otherwise asks for review of general or individual tolerancing | No inferred class |
| A.7 | Projection symbol matches layout | Automated: template symbol (first/third) present and equal to the views' projection | Hint: use a template with the right symbol |
| A.8 | Nothing overlaps the title block | Automated: views, dimension texts and lines, and notes against the title-block box and the frame | Views: `move_group` / `move_view` above the title block (**auto**). Dimensions: `move_dimension` to the nearest free spot (**auto** when one is found). Notes: `set_note y` (**auto**). Views outside the frame: hint to use a smaller scale or a larger sheet. |

## B. Lines ([guideline 01](../guidelines/01-lines-and-weights.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| B.1 | Visible thicker than hidden/centre | Automated: line widths per view. Headless, TechDraw's line-group defaults are used. | - |
| B.2 | Hidden lines dashed thin | Automated: widths consistent across all orthographic views (WARN otherwise) | - |
| B.3 | Centre lines on circular features | Automated: every full circle in an orthographic view has a centre mark | `add_centerlines` per view (**auto**) |
| B.4 | Cutting-plane lines distinct | Visual REVIEW when sections exist, otherwise n/a | - |
| B.5 | Dimension/extension/leader lines thin | Always PASS: TechDraw draws these with the thin line | - |

## C. Views & projection ([guideline 02](../guidelines/02-views-and-projection.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| C.1 | Front view shows the function | Automated WARN if the front view has more hidden than visible edges, otherwise visual REVIEW | Hint |
| C.2 | First/third-angle layout | Automated: each projection's position relative to the front view (side and alignment ±0.5 mm) | `move_view` to the correct position (**auto**) |
| C.3 | No redundant views | Automated WARN: an orthographic view other than the front carries no dimension | `delete` the view (needs input: you decide) |
| C.4 | Removed/rotated views lettered | Automated: views outside the projection group, and sections/details, have a caption or letter | `set_view caption: "VIEW A"` (**auto**) |
| C.5 | Pictorial view is supplementary | Automated: no dimension references the isometric view | `delete` those dimensions (needs input) |
| C.6 | Hidden lines visible | Automated: `HardHidden` on every orthographic view | `set_view hard_hidden: true` (**auto**) |
| C.7 | Compact shaded isometric 1 : 2 bottom-right | Automated: isometric present, shaded, at 1 : 2 (WARN when reduced to fit), in the bottom-right zone above the title block | Wrong place: `move_view` (**auto**). Missing or unshaded: hint to recreate. |
| C.8 | Only the isometric is shaded | Automated: no orthographic view is shaded | - |

## D. Sections, details, auxiliary views ([guideline 03](../guidelines/03-section-detail-broken-auxiliary.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| D.1 | Labelled cutting planes | Automated: every section view has a section symbol (n/a without sections) | - |
| D.2 | Hatching consistent | Visual REVIEW (n/a without sections) | - |
| D.3 | Shafts/fasteners not sectioned lengthwise | Visual REVIEW (n/a without sections) | - |
| D.4 | Detail views show parent and scale | Automated WARN: detail without a reference letter or `(n:m)` scale | - |
| D.5 | Broken/auxiliary views identified | Visual REVIEW when a free-standing, non-axis view exists, otherwise n/a | - |

## E. Dimensioning ([guideline 04](../guidelines/04-dimensioning.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| E.1 | Every essential dimension once | Automated: (1) requirements the generator could not place (`meta.unplaced`); (2) the overall size along each model axis is dimensioned (a matching ⌀ or a `t` note also counts); (3) every full circle has a size (including sizes stated inside callouts such as `⌴⌀11`) | (2) none, hint to use `add_dimension`; (3) `add_dimension Diameter` on the circle (**auto**) |
| E.2 | Clean extension/dimension lines | Automated WARN: extension line longer than half the sheet height. Otherwise visual REVIEW. | - |
| E.3 | Readable text orientation | Always PASS: ISO-oriented style is set on every dimension | - |
| E.4 | Correct symbols ⌀ R … | Automated: `Diameter` dimensions show ⌀/Ø, `Radius` dimensions start with R/SR/CR/RMIN/RMAX | `set_dimension format_spec` with the symbol inserted (**auto**) |
| E.5 | No dimensions to hidden lines | Automated WARN: dimension references hidden geometry | Hint: dimension in a view where the feature is visible, or add a section |
| E.6 | Baseline/ordinate first | Automated WARN: linear dimensions in one view and direction form a chain (no shared datum vertex) | Hint: re-reference to the shared datum |
| E.7 | Radius variants explicit | PASS when radii exist (the R prefix is enforced by E.4), otherwise n/a | - |
| E.8 | Multiplicity n× | Automated: (1) several separate callouts of the same ⌀/R in one view; (2) one ⌀ callout sizing several identical circles without `n×` | (1) `delete` the extras and set `n× …` on the remaining one (**auto**); (2) `set_dimension` with the `n×` prefix (**auto**) |
| E.9 | Reference / theoretically exact dimensions | Bracketed text requires semantic review | Never strips brackets automatically |
| E.10 | No overlaps | Automated, in page coordinates: text against text, text against other dimension lines, part geometry and notes; also dimension lines crossing each other (WARN) | `move_dimension` to the nearest free spot (**auto** when found; otherwise hint to move the view or use a larger sheet). Crossings: no fix. |
| E.11 | No controlling dimension twice | Automated: same dimension signature (same model feature, same value) appears more than once | `delete` the duplicates (**auto**) |

## F. Tolerances & fits ([guideline 05](../guidelines/05-tolerances-general-and-fits.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| F.1 | Tolerance scheme | Recognises size classes; warns on legacy H/K/L; ISO 22081 needs values/scope/datums reviewed; missing scheme fails | No inferred tolerance values |
| F.2 | Fit letter case | Automated: fit codes on holes are uppercase, on shafts/bosses lowercase (n/a without fit codes) | - |
| F.3 | Consistent deviation style | Automated WARN: symmetric and asymmetric tolerances mixed | - |
| F.4 | Reference dimensions non-controlling | PASS when `REF` dimensions exist, otherwise n/a | - |
| F.5 | Basic dimensions with GD&T | Automated WARN: theoretically exact dimensions without any feature control frame | - |

## G. GD&T ([guideline 06](../guidelines/06-gdt.md)), n/a unless GD&T symbols are on the sheet

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| G.1 | ISO 8015 declared | Automated: `8015` anywhere on the sheet | Append `TOLERANCING PRINCIPLE: ISO 8015` to the notes (**auto**) |
| G.2 | Feature control frame order | Visual REVIEW | - |
| G.3 | Datums attached to real features | Visual REVIEW | - |
| G.4 | Ⓜ/Ⓛ after the value | Visual REVIEW | - |
| G.5 | ⌀ only for cylindrical zones | Visual REVIEW | - |

## H. Surface texture ([guideline 07](../guidelines/07-surface-texture.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| H.1 | Parameter + value notation | Automated: a default `Ra/Rz … µm` is stated (WARN if missing), and every surface spec has parameter, value and unit (FAIL otherwise) | Append `U.O.S. SURFACE: Ra 3.2 µm` (needs input: choose the finish) |
| H.2 | Parameter name explicit | Automated: PASS when Ra/Rz present, otherwise n/a | - |
| H.3 | Local notes override default | Visual REVIEW when surface symbols exist | - |
| H.4 | Bar/circle variants | Visual REVIEW when surface symbols exist | - |
| H.5 | Lay specified | Visual REVIEW when surface symbols exist | - |

## I. Welding ([guideline 08](../guidelines/08-welding.md)), n/a without weld symbols

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| I.1 | ISO system declared | Automated: `2553` on the sheet | Append `WELDING SYMBOLS: ISO 2553 SYSTEM A` (**auto**) |
| I.2 | Arrow/other side | Visual REVIEW | - |
| I.3 | Size/length placement | Visual REVIEW | - |

## J. Threads & machining ([guideline 09](../guidelines/09-machining-threads.md)), n/a without thread callouts

Callouts are recognised in all sheet text: metric `M8×1.25-6H`, UN, NPT/NPTF, G and R pipe threads, and Tr.

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| J.1 | Internal/external graphics | Visual REVIEW | - |
| J.2 | Family, size, pitch, class complete | Automated WARN: metric callout without pitch or class | Hint: e.g. `M8×1.25-6H` |
| J.3 | Depth / THRU / LH | Automated WARN: thread without `THRU`, `DEEP` or `↧` | Hint |
| J.4 | Pipe thread family explicit | PASS when NPT/G/R callouts exist, otherwise n/a | - |
| J.5 | Counterbores/countersinks called out | Visual REVIEW | - |

## K. Material & processes ([guideline 10](../guidelines/10-material-and-heat-treatment.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| K.1 | Material callout | Automated: title-block material not empty and not the template default | `set_title_block part_material: "<material, e.g. EN AW-6082 T6>"` (needs input; never guessed) |
| K.2 | Heat treatment states hardness/depth | Automated WARN: hardening/nitriding/annealing note without HRC/HV/HB or CHD/NHD (n/a without heat treatment) | - |
| K.3 | Coating class/colour/thickness | Automated WARN: coating note without class, colour or thickness (n/a without coating) | - |
| K.4 | Masked zones | PASS if `MASK` is stated, otherwise visual REVIEW (n/a without coating) | - |

## L. Revisions ([guideline 11](../guidelines/11-revisions.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| L.1 | Title-block revision = latest row | Automated: revision not empty, not a skip letter (I O Q S X Z), equal to the last revision-table row | `set_title_block revision_index` (`A`, or the latest row) (**auto**) |
| L.2 | Revision rows complete | Automated WARN: a row without description, date or approval (n/a on first issue) | - |
| L.3 | Revision balloons | Visual REVIEW when there is more than one revision (n/a otherwise) | - |

## M. BOM ([guideline 12](../guidelines/12-bom.md)), n/a for single parts

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| M.1 | Parts list present | Automated: an annotation with an `ITEM … QTY` header | - |
| M.2 | Item numbers match balloons | Automated: number of balloons equals number of parts-list rows | - |
| M.3 | Standard parts identified | Visual REVIEW | - |

## N. Symbols & notes ([guidelines 13](../guidelines/13-symbols-and-notation.md), [14](../guidelines/14-edges-and-notes.md))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| N.1 | Consistent symbols | Automated WARN: mixed diameter signs (⌀ / Ø / ø) in dimensions | `set_dimension` normalising to ⌀ (**auto**) |
| N.2 | General notes define defaults | Automated WARN: no notes block with at least two lines | Add a note with `UNITS: mm` (needs input) |
| N.3 | Edge requirements | Numeric BREAK note recognised; vague DEBURR warns; missing requirement needs design review | No inferred edge allowance |
| N.4 | Seating/sealing edges explicit | Visual REVIEW | - |

## O. Sheet metal ([guideline 15](../guidelines/15-sheet-metal-bends.md)), applies when bend callouts (`UP/DN n°`) are on the sheet or the part is classified as sheet metal

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| O.1 | Bend direction, angle, radius | Automated: `UP/DN n° R…` callouts (WARN otherwise) | - |
| O.2 | Bend order | Visual REVIEW | - |
| O.3 | Bend relief | Visual REVIEW | - |
| O.4 | Flat-pattern basis | Automated: `K =` or a bend table/allowance stated (WARN otherwise) | - |

## P. Project overrides ([guidelines README](../guidelines/README.md#project-overrides))

| Id | Checklist item | Check | Fix offered |
|---|---|---|---|
| P.1 | No dual units / unit suffixes | Automated: `ShowUnits`, `mm`/`in` or `[` in dimension text | `set_dimension show_units: false` (**auto**) |
| P.2 | Regenerated by the current automation | Automated: generator metadata present on the page (WARN otherwise) | Hint: recreate with `create_drawing` |
| P.3 | Exact geometry extraction valid | Always PASS: views are TechDraw's exact hidden-line projection of the B-rep | - |
| P.4 | Reference-dimension meaning | Parentheses require review | Via E.9; no automatic conversion to controlling dimensions |
| P.5 | Dedupe pass ran | Automated: dedupe metadata set and no duplicate signatures remain | `dedupe_dimensions` (**auto**) |
| P.6 | *(extra, not in CHECKLIST.md)* 1 : 1 preferred | Automated WARN: views at a reduced scale would fit at the next larger scale | Hint: recreate with `scale="auto"` or `set_view scale` |

`list_drawing_rules` reads CHECKLIST.md, so it lists A.1-P.5. P.6 appears only in reports.

## Using the rules from Python

```python
import json
from free_drawing_mcp.rules import inspect, highlights

snap = json.load(open("tests/fixtures/plate_hole_pattern.snapshot.json", encoding="utf-8"))
report = inspect(snap)                       # or inspect(snap, sections=["E"]), rules=["E.10"]
print(report.text())                         # compact agent-friendly report
report.auto_actions()                        # every 'auto' fix as action dicts
report.to_dict(include_pass=True)            # machine-readable
highlights(report)                           # boxes for render_drawing
```

To add a rule, extend the matching `rules_<section>` function in `src/free_drawing_mcp/rules/checks.py`. It should yield `Finding`s with the checklist id, and a `fix` list plus `auto=True` only when the fix is safe without human input. A crashing section is reported as a WARN `<section>.*` and does not hide the other sections.
