# From a colleague's STEP file to a reviewed Swiss drawing

Use this sequence: **preserve → clarify → generate → inspect → correct → review → export → archive**. The tool creates a useful draft. A STEP model alone usually does not provide the manufacturing specification needed to release it.

## 1. Preserve the received model

Keep the original STEP unchanged, with the colleague's revision and date. Work in a separate job folder, for example:

```text
jobs/BRACKET-042/
  input/bracket_revB.step
  output/bracket_revB_draft.FCStd
  output/bracket_revB_review.png
  output/bracket_revB_snapshot.json
  output/bracket_revB_inspection.json
  release/
```

Create the directories before writing output. Record the input hash if traceability matters. A later model revision should get a new working drawing; do not overwrite the approved revision.

## 2. Establish what the drawing must communicate

Collect available information from the colleague, existing drawing or project specification:

- part or assembly; drawing number, title and revision;
- units and a known overall size to check import scaling;
- material and manufacturing state: machined, cast, formed, coated, etc.;
- functional interfaces, fits, datum features and tolerances;
- surface texture, edge treatment, threads and special processes;
- applicable SN EN ISO editions, house template and approval responsibility.

Start the geometric draft with what is known. Keep missing requirements on an open-items list. Do not invent `ISO 2768-mK`, `Ra 3.2`, a material or an edge break merely to clear inspection findings. Modelled holes are not proof of a thread specification; imported PMI is not guaranteed to be extracted by this tool.

The modelled-thread detector does not supply a tolerance class. Confirm the intended class and add it to the callout before release. Older drawings generated before 2026-10-29 may contain assumed `6H` or `6g` classes that need review.

## 3. Check the backend and generate a draft

The MCP route offers the most control. Call `drawing_status()` first. The local installation is `C:/Code/free-drawing`, with FreeCAD 1.1 detected in `C:/Program Files/FreeCAD 1.1/bin/`. Other computers must discover their own paths. Headless mode needs no running GUI; the RPC backend requires the addon server in FreeCAD.

Example MCP call, with only known requirements supplied:

```python
create_drawing(
    source="C:/jobs/BRACKET-042/input/bracket_revB.step",
    output="C:/jobs/BRACKET-042/output/bracket_revB_draft.FCStd",
    template="A3",
    projection="first",
    scale="auto",
    title="Mounting bracket",
    drawing_number="BRACKET-042",
    title_block={"revision_index": "B"},
    sections=True,
    details=True,
)
```

These are tool-call examples, not functions automatically available in an ordinary Python session. Through a chat agent, use the prompt at the end of this guide.

For a quick terminal draft:

```powershell
Set-Location C:\Code\free-drawing
uv sync --group dev
uv run free-drawing-check make "C:\jobs\BRACKET-042\input\bracket_revB.step" -o "C:\jobs\BRACKET-042\output\bracket_revB_draft.FCStd" --png "C:\jobs\BRACKET-042\output\bracket_revB_review.png" --backend headless
```

The current CLI supports make/inspect/autofix/render. It does not expose all MCP metadata, template or export options. Exit code 1 can mean that the drawing was created but inspection found unresolved requirements; read the report and check the output file before treating it as a process failure.

## 4. Check the imported geometry and selected views

Compare a known size and the solid/component count with the received model. Read import repair and alignment warnings. Confirm that the front view reflects function, first-angle placement matches the projection symbol, and the selected views explain all relevant features.

The source can be automatically aligned for projection. Coordinates used for later cutting planes refer to the aligned working geometry, not necessarily the colleague's original coordinate frame. Inspect the snapshot or working model before specifying a cut origin.

Use sections for internal steps, counterbores, blind holes and intersecting passages. Use details for small grooves, reliefs or crowded features. Verify that cuts pass through the intended features, voids are not hatched, and parent letters/arrows correspond to the child view. Half sections require actual symmetry.

Inclined bores, freeform faces, assembly exclusions, ribs, broken-out cuts and offset sections may require manual FreeCAD TechDraw work. The current generator does not automatically solve every one of these cases.

## 5. Inspect and apply bounded corrections

```python
inspect_drawing(doc="C:/jobs/BRACKET-042/output/bracket_revB_draft.FCStd",
                output="json", render=True)
autofix_drawing(doc="C:/jobs/BRACKET-042/output/bracket_revB_draft.FCStd",
                max_passes=3, render=True)
```

Use the rule IDs to work through the report:

| Finding | Next action |
|---|---|
| A.7 | Match the template projection symbol to the view arrangement |
| A.8 / E.10 | Move annotations, improve view spacing or use a larger sheet; inspect the new render |
| D.1 / D.2 | Resolve missing cut markers, failed view construction, hatch errors or insufficient space |
| E.1 | Resolve unplaced dimensions or unsupported feature definition; check whether a reported overall is already correctly represented by a thread/diameter |
| E.5 | Prefer visible geometry in a section or auxiliary view over hidden-line dimension targets |
| E.9 / P.4 | Verify reference or theoretically exact meaning; never strip parentheses blindly |
| E.11 | Check duplicate controlling dimensions before deleting anything |
| F.1 / K.1 | Obtain the real tolerance scheme or material; these cannot be inferred from geometry |

Autofix is a convenience, not a release action. Inspect every applied action's result and the regenerated sheet. Stop repeating a fix that does not improve the finding. Add supplied requirements with `apply_drawing_actions`; use actual snapshot object names. Manual section/detail actions exist, but confirm their geometry and placement explicitly. If the current sheet cannot hold the required views, regenerate with a larger template into a new draft. Record manual edits first, because regeneration does not replay them.

## 6. Review the engineering specification

Use [the Swiss policy](../guidelines/16-swiss-tpd-gps.md) and [checklist](../guidelines/CHECKLIST.md). Confirm functional dimensions, datum references, fits, tolerance values, finish and edge requirements with the responsible designer. Check sheets at their intended print size. The default baseline origin is a geometric convenience, not a selected GPS datum.

`PASS` means the implemented automated checks found no failures or warnings. It can still contain REVIEW items. It does not prove complete SN EN ISO compliance, feature coverage, manufacturability or release approval. A score of 95 with a missing material is still an incomplete drawing.

## 7. Reopen, export and inspect the deliverables

Save the FCStd, reopen it in a fresh FreeCAD process, and inspect again. Capture the final snapshot and report:

```python
get_drawing_snapshot(doc="C:/jobs/BRACKET-042/output/bracket_revB_draft.FCStd",
                     include_geometry=True)
export_drawing(doc="C:/jobs/BRACKET-042/output/bracket_revB_draft.FCStd",
               format="pdf", path="C:/jobs/BRACKET-042/output/bracket_revB_review.pdf")
```

Save the returned JSON to the job folder. Open the actual PDF and verify glyphs, true values, cutting-plane arrows, hatching, scale captions and clipping. The headless renderer is schematic; GUI TechDraw export is a different path. A good PNG preview is not proof that a different PDF export path is identical. DXF is an optional downstream deliverable, not a substitute for a fully annotated drawing.

## 8. Release and archive

Keep the drawing **In preparation** until the responsible reviewer accepts it. Archive the source STEP, editable FCStd, reviewed PDF, snapshot/report, specification decisions, standards editions and approval record together. Keep unresolved items visible. Do not send the drawing to a colleague or supplier unless the user has asked you to send it.

## Reusable agent prompt

> Use $freecad-swiss-drawings to create a reviewed draft from `C:/jobs/BRACKET-042/input/bracket_revB.step`. Save new files under `C:/jobs/BRACKET-042/output`. Use first-angle Swiss SN EN ISO practice, automatic sections/details and the project guidelines. Preserve the source. Start with known geometry, list missing manufacturing requirements, inspect and correct the draft, reopen it, and provide the FCStd, PDF, preview and inspection report. Keep it In preparation until I provide the missing requirements and approve release.
