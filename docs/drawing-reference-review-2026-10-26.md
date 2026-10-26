# STEP drawing reference review — 26 October 2026

This review informed changes to the existing FreeCAD generator. The target is a small set of large standard views at one shared scale, with readable paper-space annotations. Every defining view must carry a dimension. The coloured isometric is retained without dimensions at the bottom right. Sections and details are exceptions for hidden or genuinely small features.

## Projects reviewed

| Project and evidence | Useful approach | Decision in this implementation |
| --- | --- | --- |
| [Draftwright](https://github.com/pzfreo/draftwright), source and layout primer at `f1a5db9b4490e9b233ae5b7cac1138633929c693` | Collect annotation demand before layout; measure complete view blocks; preserve coverage when comparing alternatives. | Implemented independent footprint packing and coverage guards. AGPL-3.0 implementation was inspected, not copied or bundled. |
| [freecad-automation](https://github.com/dooosp/freecad-automation), dimension planning and traceability source at `659405b3e36d01d21027f0c75db3f7e36ded159b` | Link a dimension to a specific feature and rendered evidence. Successful export alone is not a quality gate. | Replanning tracks requirement IDs, checks geometry references and values, and reports unresolved layout separately. MIT. |
| [build123d-drafting-helpers](https://github.com/pzfreo/build123d-drafting-helpers), documentation and licence at `9217f8204b58d79fd75a078d0904e497d88fc503` | Preserve full text and font size for short dimensions; include shoulders and outside arrows in layout. | Added exported-stroke footprints to inspection and placement. Kept the existing osifont renderer. Apache-2.0; no code copied. |
| [step-to-drawing](https://github.com/getvenkateshprasad-sys/step-to-drawing), engine at `2421bd7cd8688684099793754dca082b9dcfcc2b` | Editable FreeCAD output, local wall-thickness checks for holes, explicit projection arrangement. | Retained native editable dimensions. Did not adopt diameter-only hole deduplication, automatic assembly fusion, unconditional sections, or inferred manufacturing tolerances. README declares MIT; no separate licence found in the inspected tree. |
| [FreeCAD TechDrawTools](https://github.com/FreeCAD/FreeCAD-macros/blob/master/TechDraw/TechDrawTools.FCMacro), macro documentation/source excerpt, version 0.4.5 | Coordinate dimension stacks, consistent spacing, and section alignment. | Retained geometric datum references and 8 mm paper spacing. Added bounded alternatives for stack sides. LGPL v2; no code copied. |
| [FreeCAD Drawing Dimensioning](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Drawing_Dimensioning_Addon.md), official historical documentation | Interactive dimension placement and editing. | Rejected as an engine dependency: the documented workflow lacks parametric updating and uses the superseded Drawing workbench. |
| [step2dxf](https://github.com/jaschawilcox/step2dxf), source at `ca653252cd92219514789290e9a3c4156b5cc5eb` | Recognise plate faces before producing cutting profiles. | Useful for a separate fabrication-profile workflow, not a substitute for dimensioned manufacturing sheets. No licence file found; no code copied. |
| [ClearHandoff Autodraft](https://www.clearhandoff.com/en/help/autodraft/), product documentation | Geometry-derived views followed by editable drawing review. | Supports the existing editable-FCStd workflow. Closed implementation; product claims were not treated as verified algorithms. |
| [CAD2Drawing](https://cad2drawing.com/), product documentation | Select a dimension to identify its model feature; keep unspecified design requirements explicit. | Preserved model-derived measurements and missing material/tolerance findings. Closed implementation; not executed. |
| [Drawlytic](https://drawlytic.com/generate-technical-drawings), product documentation | Exact B-rep hidden-line projection and editable dimensions. | Retained FreeCAD's geometric projection. Closed implementation; no comparative performance claim. |

The external engines were not benchmarked against each other. “Adopted” here means a design idea was independently implemented and tested in this repository, not that every claim in another project's README was verified. Downloaded source excerpts remain in `.scratch/reference-projects/`.

## Drawing datasets

[Cadsy CAD Technical Drawings](https://huggingface.co/datasets/cadsy/cad-technical-drawings) supplies paired STEP geometry and labelled ISO/ASME-style sheets. Revision `01a38048c5eaf09b777d7929df8e3c8a03e36965` contains 65 parts. Six evenly spaced IDs were selected before running the generator: `0001`, `0013`, `0026`, `0039`, `0052`, `0065`. The dataset is Apache-2.0. Its author explicitly identifies the sheets as synthetic, not certified manufacturing drawings. Annotation counts are comparison data, not completeness targets.

[eDOCr](https://github.com/javvi51/eDOCr) provides mechanical drawing examples. Adapterplatte, Gripper and Halter at revision `1786f898e0278fcc4574ce84a23ca6bad0c170e7` were fetched for visual review. They illustrate characteristic views, selected repeated-feature callouts, and readable external annotations. The repository declares MIT; original sheet rights and engineering approval were not independently verified. These files remain local and are not redistributed.

The [ViewConsNet dataset](https://data.mendeley.com/datasets/n5w53gtj2r) was also considered. Its view-consistency task is relevant, but paired STEP inputs were not established, so it was not used for generator comparisons.

## Changes and limits

- Dimensioned-only output is the default. Projection anchors remain hidden when necessary to preserve dependent geometry.
- An automatic 90° orientation trial can improve the shared standard-view scale without losing requirements or adding hidden dimensions. Explicit view requests and turned-part orientation are preserved.
- Scale selection compares ordinary and balanced baseline stacks, then repacks measured view and annotation footprints. The standard-view group keeps its projection alignment while its unused corners can fit around the title block. General notes can move; thickness notes stay with their view. Text remains 3.5 mm; dimension lines are 0.35 mm. These are project readability choices.
- Replanning must preserve every already placed requirement, not merely the dimension count. Measurements and reference identities are checked before accepting scale changes.
- Standard views retain readable sizes and locations. Automatic strict output uses at most one section and one detail. Sections need an unresolved/hidden requirement. Details target clusters of small features; a single small chamfer or radius uses an external callout on its standard view.
- Annotation movement checks part outlines, centre lines, detail circles, other text, rendered leaders, own extension lines, and crossing/overlapping dimension lines. Captions in schematic exports sit below their dimension stacks.
- Radius leaders attach to the referenced arc, not an arbitrary point on the corresponding full circle. This prevents leaders from pointing into empty space or through the title block.
- The packing search is bounded; it does not prove a global optimum. Existing detail scales constrain standard-view enlargement because changing a FreeCAD detail scale produced stale measured geometry during testing.
- Caption placement is verified for the schematic PDF/SVG renderer. Native TechDraw GUI caption positions can differ and need separate visual review.
- Unsupported geometry, unplaced dimensions, unresolved collisions, and missing specifications remain findings. They are not erased to improve a score.

## Repeat the dataset evaluation

```powershell
uv run python samples/reference_dataset.py fetch --out .scratch/drawing-references
uv run python samples/reference_dataset.py evaluate --data .scratch/drawing-references --out samples/validation/reference-new
uv run --group dev pytest -q
```

Use a new output directory for each evaluation. The downloader records pinned URLs and SHA-256 hashes. Evaluation verifies input hashes, generates each part, reopens each FCStd in another FreeCAD process, compares dimension values and references, and exports a PDF and PNG. `results.json` includes code hashes, scales, dimension counts by view, empty views, collision evidence and inspection findings. It does not use image similarity as proof of engineering correctness.
