# Swiss drawing practice: SN EN ISO TPD and GPS

Project policy updated 2026-10-17. This guide governs new generated drawings where older topic sheets conflict. It is an engineering implementation guide, not a claim of certified standards compliance. The [source register](sources/README.md) distinguishes catalogue verification, public previews and material still requiring the full standard.

## Choose the specification before drawing

Use the Swiss adoption of the applicable EN ISO standard and record its edition in the drawing specification or controlled project register. A Swiss drawing is not defined by a font, a language or an invented national tolerance class. Contract requirements, the declared GPS system and an unambiguous definition of the part govern it.

The project uses millimetres, first-angle projection with a matching symbol, ISO sheet sizes and preferred scales. These are configurable project conventions. A3, 1:1 preference, line group 0.7, 3.5 mm text, baseline spacing and the shaded isometric location are house settings, not universal SN EN ISO requirements. Use fewer views only when the remaining representation still defines the part.

Do not derive material, fit, surface texture, edge allowance, heat treatment, functional datum features or tolerance values from nominal B-rep geometry. A geometric origin used for baseline dimensions is not automatically a GPS datum. The generator leaves manufacturing requirements unspecified and the inspector requests design input.

## Applicable systems

| Subject | Standard family / edition anchor | Implementation policy |
|---|---|---|
| Views, sections and cuts | SN EN ISO 128-3:2022; ISO 5456-2 | Parent reference, cutting direction, material-only hatch and identifiable view |
| Lines, dimensions, scales | ISO 128-2; ISO 129-1; ISO 5455 | Distinct line functions, true values, readable labels; verify adopted editions before release |
| Sheets / title blocks | ISO 5457; SN EN ISO 7200 | Document identity, owner, revision, status and approval remain traceable |
| GPS framework | SN EN ISO 14638; ISO 8015:2011 | Specify and inspect the complete product definition; apply independence unless a stated relationship modifies it |
| Size and fits | ISO 14405 series; ISO 286 series | Distinguish size from location; choose fit from function |
| Geometry / datums | ISO 1101; ISO 5459; ISO 2692 | Identify datum features and precedence; material requirements require explicit selection |
| General specifications | ISO 22081:2021 | Define actual values, applicability and datum system; the standard number alone is insufficient |
| Profile surface texture | ISO 21920 series; indication part 1:2021 | Complete symbol and specification operator; do not automatically translate a legacy Ra note |
| Edges | ISO 13715; ISO 21204 | Undefined edge deviations and defined transitions are different requirements |
| Digital product definition | ISO 16792 | Identify the authoritative model/drawing and maintain annotation-to-feature association |

Edition anchors above are supported by the register. Undated family entries identify the standards to consult, not a verified claim about their latest edition.

## View selection and special geometry

| Geometry / functional need | Preferred representation | Required checks |
|---|---|---|
| Blind holes, stepped bores, internal pockets | Full section through the relevant axis | Bottom shape, usable cylindrical depth and entry side must be clear |
| Symmetric hollow part | Half section when symmetry is established | Shared centre line; preserve the outside half; no invented symmetry |
| Non-collinear passages | Several full sections or an explicitly designed offset section | Each passage location stays traceable; bends of the cutting plane are visible |
| Rib / web cut along its length | Conventional unhatched rib representation | Transverse cuts are different; do not exempt every rib or shaft from every cut |
| Fasteners / multi-part assembly | Selective section and distinct adjacent-part hatching | Preserve component identity; do not fuse parts merely to simplify a cut |
| Small groove, relief, seal edge | Enlarged detail with parent boundary, unique letter and scale | Dimension the characteristic geometry once on the clearer view |
| Inclined bore or face | Auxiliary view normal to the feature, plus appropriate section | Elliptical projection is not a diameter; projected length is not true length |
| Long uniform member | Clearly marked broken view if justified | Dimensions retain the full model length |
| Freeform / loft / spline surfaces | Functional sections and model-based profile specification | Overall dimensions alone do not fully define the surface |
| Sheet metal | Formed views plus appropriate flat pattern / bend data | Thickness, inside radius, direction and manufacturing state are explicit |

Hidden lines can be suppressed when they reduce clarity, especially in sections. Keeping them on in principal views is a house default. The 3 mm detail trigger and hatch spacing are readability heuristics, not standard-mandated limits. Check the final export at its intended printed size.

## GPS decisions that must remain explicit

- Specify functional datum features from assembly and inspection needs. Avoid arbitrary A/B/C labels assigned solely from the largest faces.
- Distinguish a directly toleranced size, a theoretically exact dimension (TED), and an auxiliary/reference dimension. Box TEDs where the geometrical specification requires them; reference dimensions may use parentheses. Do not make a reference dimension controlling.
- Under ISO GPS, do not import an ASME envelope rule or an RFS symbol by habit. State envelope or material requirements where applicable and supported by the selected standard.
- Keep legacy ISO 2768-2 H/K/L requirements traceable on existing drawings. ISO 22081 does not provide a mechanical substitution for a legacy `mK` note. ISO 2768-1 size classes are a separate issue.
- Choose surface texture and edge treatment for function. A bare `Ra 3.2` or a blanket edge break is not a general solution for a complex part.
- Avoid dimension chains and duplicate controlling requirements. A detail or section can carry a requirement previously on the parent; regenerate its geometric references and remove the old controlling instance.

## Release and test evidence

Generated drawings remain **In preparation**. A machine PASS only means that implemented checks passed; REVIEW items and unrecognised geometry still need an engineering decision. The current engine does not automatically author a complete datum system, GPS frames, auxiliary views, offset/broken-out cuts, rib exemptions or broken views.

For each new shape family, preserve the STEP, drawing, snapshot and rendered sheet. Check cutting-plane intersection, section area, voids free of hatch, unique references, view scales, true dimension values, duplicate requirements, unplaced items and annotation collisions. Reopen the saved FCStd and inspect again. Include rotated and translated models, reductions/enlargements, both projection methods, small features and crowded sheets. Record unsupported cases rather than silently accepting them.

The Normen-Auszug 2026 sample provides side-by-side shaft examples using legacy general tolerances, ISO 22081 and model-linked definition. Use them to review clarity and traceability, not to copy their part-specific tolerances. Its full explanations are not present in the sample.
