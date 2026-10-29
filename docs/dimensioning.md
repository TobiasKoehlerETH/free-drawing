# The dimensioning engine

`create_drawing` uses a general pipeline to identify dimension requirements from the 3D B-rep and place them on drawing views. Unsupported features remain explicit gaps. A successful export does not establish a complete definition; the [independent reconstruction check](drawing-reconstruction-design.md) provides a separate, scoped geometric check.

```
importer.read_shape / align          any STEP -> repaired, aligned solid
analysis.classify                    plate | turned | prismatic
features.requirements                3D: what must be stated, exactly once
builder.choose_layout                which front direction, which views
planner.realize                      2D: which view, which vertices/edges
builder (augmentation loop)          add views until complete, drop empty ones
builder (scale + layout)             largest ISO scale that fits, keep-outs
dimensions.place                     stacks, leaders, collision avoidance
dimensions.dedupe                    nothing controlling twice
snapshot.set_meta                    unplaced / dedupe_ran / ... for the inspector
```

All modules are in `addon/FreeDrawingMCP/fd_drawing/`.

## 1. Datums and the aligned frame

`importer.align` rotates the part into its own principal frame: the largest planar faces, the cylinder axis, or the inertia axes, in that order of preference. After alignment, model X/Y/Z are true view directions, even for STEP files exported at an arbitrary angle.

The **datum** for each axis is the minimum face of the exact bounding box (`lo[X]`, `lo[Y]`, `lo[Z]`). Every position requirement is measured from that datum. The datum plane itself is never dimensioned. This is what produces baseline/ordinate dimensioning instead of chains ([04](../guidelines/04-dimensioning.md), checklist E.6, project override "ordinate first").

## 2. Requirements (`features.py`)

A *requirement* is one controlling piece of information the drawing must state **exactly once** ([04](../guidelines/04-dimensioning.md) "state each essential size once", E.1, E.11). Requirements are computed in 3D, independent of any view.

| Kind | Source geometry | Text |
|---|---|---|
| `loc` | Position of a planar face, a hole/boss/pattern centre, a slot-end arc centre or an inclined-face edge along one axis, from that axis' datum | `%.2w` (value only) |
| `dia` (hole) | Hole stack: coaxial internal cylinders and cones on one axis line | `4× ⌀12 THRU`, `⌀8 ↧15`, `2× ⌀6.6 THRU ⌴⌀11 ↧6.8`, `⌀6.4 THRU ⌵⌀12×90°` |
| `dia` (boss / turned) | Full external cylinder | `⌀30` |
| `pcd` | ≥ 3 identical holes, equal radius and equal angular pitch | `⌀90` pitch circle, hole callout gets ` EQ SP` |
| `radius` | Partial cylinders and toroidal fillets, grouped by radius and axis | `4× R5` |
| `sphere` | Spherical faces | `SR…` |
| `chamfer` | 45° narrow planar faces (≤ 20 % of the smallest extent) and 45° cones on shafts/holes, grouped by leg | `2× C1.5` |
| `angle` | Inclined planar face that is not a chamfer; its edges also produce `loc` requirements | `30°` |

Details:

- **Hole stacks.** Connected internal cylinders are grouped by axis line. The smallest radius is the hole and a larger coaxial cylinder is the counterbore. Connected internal cones can define countersinks. *THRU* requires exterior openings at both ends; opening into another internal passage is insufficient. Separate coaxial blind holes remain distinct. Group signatures include depth, counterbore, countersink, entry direction, and whether the countersink is on both ends. Identical holes entering opposite faces receive separate callouts. One-sided counterbores and countersinks retain their entry direction even on a through hole.
- **Hole locations.** Each hole centre is located along the two other axes, except when the hole is part of a PCD pattern, or is concentric with an outer cylinder (tube bore, flange bore). Those are already defined by the ⌀ and the PCD.
- **Bolt circles.** 2×2 patterns at 45° are left to coordinate dimensioning, which is clearer for rectangular layouts. A pattern centre that is not concentric with an outer cylinder is located like a hole.
- **Bosses.** A cylinder that forms the outer boundary of the part is not located: its ⌀ and the overall size already fix it. A smaller boss concentric with an enclosing cylinder shares that axis. An offset boss still needs its centre locations.
- **Overall sizes.** One overall size is always required per axis, unless an outer diameter already spans that axis (a flange has no separate 120 × 120).
- **Merging.** `loc` requirements on the same axis and coordinate are merged. The strongest source wins (overall > hole centre > face), and centres beat faces.
- **Assemblies.** Assemblies get only their overall sizes. Detail dimensioning belongs on the part drawings.

## 3. View selection (`builder.choose_layout`, `planner.needs`)

Each requirement says what a view must offer to show it *characteristically* ([02](../guidelines/02-views-and-projection.md)):

| Requirement | Needs |
|---|---|
| `loc` along A | a view with A **in the plane** (the face is seen edge-on) |
| hole / boss `dia` on axis A | a view looking **along** A (the hole is a circle) |
| turned `dia` on axis A | a view with A **in the plane** (⌀ on the profile) |
| `radius`, `pcd` on axis A | a view looking **along** A |

The search covers six front directions (±X, ±Y, ±Z) and four view sets: `[Front]`, `[Front, Top]`, `[Front, Right]` and `[Front, Top, Right]`. The front's horizontal axis is the longest in-plane extent; for turned parts it is the turning axis, so the axis is horizontal. Candidates are ranked by:

1. number of unmet requirements (completeness first, E.1)
2. number of views (smallest set, C.3)
3. turned parts: the profile must be the front view
4. model Z up preferred
5. most requirements visible in the front view alone

**Plates.** Thickness remains a geometric requirement when views are selected. Keep an edge view with a native thickness dimension at the shared scale. A `t12` note cannot replace that view or dimension. Bent sheet metal also receives a dimension between actual wall faces, using a section when necessary.

**Ties.** Ties go to the direction that "shows more than it hides" (C.1). The score is the sum of facing-area weights, with curved faces (holes, bosses) weighted 3×, so the side with the functional features wins.

The views are created in a `TechDraw::DrawProjGroup` with first-angle or third-angle arrangement (C.2), hidden lines on (C.6), and smooth and seam edges off.

## 4. Realisation (`planner.Realizer`)

Each requirement is turned into a concrete TechDraw dimension, with a view and `VertexN`/`EdgeN` references. Requirements are processed in this order: `dia`, `pcd`, `radius`/`sphere`, `chamfer`, `angle`, `loc`. Holes are therefore assigned first, and their centre positions can follow them.

Each candidate view gets a score of **10 if the referenced geometry is visible** (E.5), plus a view priority (Front 3, Top 2, Right/Left 1), plus an affinity bonus:

- **Diameters.** In a circle view the callout is a radial `Diameter` on the circle; holes get +4 here. In a profile view it is a linear distance across the two silhouette lines; turned diameters get +4 here. Both witnesses must lie within the selected cylinder's axial extent. Entry-specific callouts use the entrance view or actual section geometry.
- **Locations.** The view needs real *feature points* for both the target coordinate and the datum: ends of lines perpendicular to the measured axis, circle centres, or corners of slanted edges. Fillet tangent points never count. When a circle centre has no TechDraw vertex, a cosmetic vertex is added. Hole centres get +4 in the view that carries the hole callout, so the positions and the ⌀ are read together.
- **Radii, spheres.** The best visible arc with that radius.
- **Chamfers.** A slanted line whose dx and dy both equal the leg, dimensioned as a linear `C` callout.
- **Angles.** Only in a view where the inclined face is edge-on (true angle), between the slanted line and an adjacent axis-parallel line.
- **PCD.** In the view along the pattern axis, as a dash-dot cosmetic circle through the hole centres plus a ⌀ callout.

Linear dimensions in one view and direction all start at the datum vertex, so they form a **baseline stack** (E.6). If no view can realise a requirement, it is appended to `unplaced` with a readable description, for example `position X=35.0 (hole-center) from datum X=0.0`. The description is stored in the page metadata and reported as an `E.1` FAIL. The tool never silently drops a requirement.

A circular outline may have no topology vertex at the baseline origin. The planner adds a cosmetic vertex at the exact circle extremum. Cosmetic witnesses use unscaled, Y-up view coordinates, are resolved again after recompute, and are reused on subsequent planning passes. This also applies to centre references, thread ends, and reconstruction repairs.

## 5. View augmentation

When the automatic layout is used, the first realisation is improved in a loop ([02](../guidelines/02-views-and-projection.md), E.1, E.5, C.3):

1. **Badness** = (number unplaced, number of dimensions on hidden lines).
2. While badness > 0, try adding `Top`, `Right`, `Left`, `Bottom`, and `Rear` in turn, up to four views. Re-realise after each addition. Keep the new view only if badness strictly decreases; otherwise remove it again. Rear views are placed outside the adjacent side view with room for both sets of annotations.
3. Remove every view that carries no dimension, then realise again.

Every change is reported in the `create_drawing` warnings, for example `added Right view to dimension features completely`.

## 6. Scale and layout

- **Space reserved per view and side**: stack depth = `10 + (n−1)·8 + 3.5 + 1.5` mm for n stacked lines, plus 16 mm on the top/right when a view carries radial callouts, plus a 12 mm gap between views.
- **Scales tried**: ISO 5455 scales from large to small: 1 : 1, 1 : 2, 1 : 5 … 1 : 100. Enlargements only with `allow_enlarge`. The first scale whose whole layout fits one of the free regions wins. The regions are: left of the title-block column; above the title block left of the isometric; and the full-width band. The keep-outs are the title block, the isometric, the notes and the parts list (A.8, P.6).
- **Isometric**: placed bottom-right above the title block, normally at 1 : 2. Tiny parts are enlarged for recognition and large parts are reduced to fit the reserved space (C.7).

## 7. Placement and collision avoidance (`dimensions.place`)

**Baseline stacks** ([04](../guidelines/04-dimensioning.md), ISO 129-1):
- Horizontal dimensions go above or below the view, vertical ones right or left. A side already used by a neighbouring projection is avoided.
- Each stack is sorted by span, **shortest closest**. The first line is 10 mm off the outline, then every 8 mm, so extension lines never cross dimension text (E.10).
- When the text is wider than the span, the label moves outside, beyond the far extension line.

**Radial callouts** (⌀, R, angles):
- The search tries leader directions 45°, 135°, −45°, −135°, then 30°/150°/…, 60°/120°/… and 80°/100°/…, at distances r + 5 + 4k mm.
- A label position is accepted only when its padded text box and its leader are clear of **every view's edges** (including neighbouring views, PCD circles and cosmetic geometry), all dimension and extension lines placed so far, other labels and other leaders. All of this is checked in page coordinates.
- Among the free positions the cheapest one wins: cost = leader length + 1.5 × direction rank. Placed labels become obstacles for the next one.

**Style** (set per object, user preferences untouched):
- Dimensions resemble SolidWorks: 0.18 mm lines, 3 mm filled arrows, 3.5 mm `osifont`, ISO-oriented, units never shown (P.1).
- Views use ISO 128 line group 0.7: 0.7 mm visible lines and 0.35 mm hidden/thin lines ([01](../guidelines/01-lines-and-weights.md)).
- Every cylinder gets dash-dot centre lines, extending 3 mm beyond the outline (B.3).

## 8. Dedupe (`dimensions.dedupe`)

Before metadata is written, every dimension gets a **signature**: its type, its value, and its references resolved to model-space features. Dimensions with the same signature (the same thing measured twice, possibly in two views) are removed and only the first is kept (E.11, P.5). `dedupe_ran` and the list of removed names are recorded. The inspector re-checks signatures, so duplicates added later by hand are caught too.

## 9. Metadata for the inspector

`snapshot.set_meta` stores the following on the page: generator, part kind, projection, views, `unplaced`, `thickness`, `dedupe_ran`, assembly components and whether the isometric is shaded. The rules engine uses these for E.1, P.2, P.5, C.7 and M.*, so the inspection knows what the generator could not do as well as what is on the sheet.

## Guideline map

| Step | Guideline | Checklist |
|---|---|---|
| Datums, baseline/ordinate stacks | [04-dimensioning](../guidelines/04-dimensioning.md), [README overrides](../guidelines/README.md#project-overrides) | E.6 |
| Requirements stated once | [04-dimensioning](../guidelines/04-dimensioning.md) | E.1, E.11 |
| Hole callouts, ⌴ ⌵ ↧, THRU, n× | [04-dimensioning](../guidelines/04-dimensioning.md), [09-machining-threads](../guidelines/09-machining-threads.md), [13-symbols-and-notation](../guidelines/13-symbols-and-notation.md) | E.4, E.8, J.3, J.5 |
| R / SR / C / angles | [04-dimensioning](../guidelines/04-dimensioning.md), [14-edges-and-notes](../guidelines/14-edges-and-notes.md) | E.4, E.7, N.4 |
| `t` note for plates / sheet | [04-dimensioning](../guidelines/04-dimensioning.md), [15-sheet-metal-bends](../guidelines/15-sheet-metal-bends.md) | E.1 |
| View selection, projection | [02-views-and-projection](../guidelines/02-views-and-projection.md) | C.1, C.2, C.3, C.6 |
| Visible geometry preferred | [04-dimensioning](../guidelines/04-dimensioning.md) | E.5 |
| Spacing 10 / 8 mm, no overlaps | [04-dimensioning](../guidelines/04-dimensioning.md) | E.2, E.10 |
| Line weights, centre lines | [01-lines-and-weights](../guidelines/01-lines-and-weights.md) | B.1, B.3 |
| Scale and layout, keep-outs | [00-sheet-and-title-block](../guidelines/00-sheet-and-title-block.md), [README overrides](../guidelines/README.md#project-overrides) | A.8, C.7, P.6 |
| Dedupe | [README overrides](../guidelines/README.md#project-overrides) | E.11, P.5 |

## Extending the engine

To add a new feature type:

1. Emit a requirement dict in `features.requirements` with a new `kind` and a `prio`.
2. Say which views can show it characteristically in `planner.needs`, so view selection accounts for it.
3. Add a `Realizer` method that produces a spec: `view`, `type`, `refs`, `format`, `role`, plus `side`/`span` for linear dimensions or `circle` for radial ones. Call it from `realize`.
4. Describe it in `planner._describe`, so an unplaced instance shows up readably in `E.1`.

Placement, collision avoidance, dedupe and inspection then work for the new kind with no further changes.
