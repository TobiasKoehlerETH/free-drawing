---
topic: Section views, detail views, broken views, auxiliary views
standards:
  - ISO 128-40:2001 — Basic conventions for cuts and sections
  - ISO 128-44:2001 — Sections on mechanical engineering drawings (now ISO 128-3:2020)
  - ISO 128-50:2001 — Areas in cuts
  - SN EN ISO 128-3:2022 — current edition; supersedes 2020
flags:
  - Parts −40 / −44 / −50 renumbered into ISO 128-3:2020
last_verified: 2026-10-17
confidence: catalogue verified; detailed practice requires engineering review
---

# 03 — Section, Detail, Broken, Auxiliary Views

For new drawings, the [Swiss TPD/GPS policy](16-swiss-tpd-gps.md) takes precedence. [ISO 128-3:2022](https://www.iso.org/standard/83356.html) supersedes the 2020 edition. The 45-degree / 2–6 mm hatch choice below is a readability convention, not a universal prescribed spacing. The current builder generates principal full/half sections and enlarged details. Offset, broken-out, broken and auxiliary views still require engineering handling.

## Rules

1. **Cutting plane**: marked by a Type H line (long-dash short-dash thin, thick at ends); arrows indicate viewing direction; identified with uppercase letters at both ends (A–A, B–B …). [1][2]
2. **Hatching**: continuous thin lines at 45°, spacing 2–6 mm chosen proportional to the hatched area. Adjacent parts are hatched in opposite directions or with different spacing. The **same part is hatched identically across all views**. [1][3]
3. **Conventional section exemptions depend on the cutting direction and component**: longitudinal cuts through ribs, webs and relevant standard parts can be represented unhatched. Transverse cuts are different. Do not exempt every shaft or rib from every section. Preserve component identity and review assemblies explicitly. [2][3]
4. **Half-section**: allowed on symmetric parts; the centre line divides sectioned from non-sectioned half. **Local / partial sections** are bordered by a Type C line (thin freehand break). [1][3]
5. **Detail views**: encircled by a thin circle or ellipse on the parent view, tagged with an uppercase letter, and re-drawn enlarged with the scale stated in parentheses, e.g. "X (5:1)". [2][3]
6. **Broken views**: use Type C (wavy) or Type D (zig-zag) break lines parallel to each other. **Dimensions across the break retain the full, true length** of the part. [2][3]
7. **Auxiliary views** are projected perpendicular to an inclined face; an arrow indicates the viewing direction, and a letter identifies the view. Only the inclined feature is shown in full. [2]

## Sources

1. ISO 128-3:2020 — "Technical product documentation (TPD) — General principles of representation — Part 3: Views, sections and cuts" — https://www.iso.org/standard/76035.html
2. Hoischen, H.; Hesser, W. — *Technisches Zeichnen*, 37. Auflage (2020), chapter 5 "Schnittdarstellungen".
3. TU München — lecture *Maschinenelemente — Technisches Zeichnen* (mec.ed.tum.de PDF).

## Confidence / flags

- **Edition control**: the historical references above explain legacy practice. Use the agreed current Swiss adoption, not an automatic 2020 citation.
- **Generation check**: verify actual cut material, voids free of hatch, parent markers, scale, relocated dimensions and sheet clearance. Failed or unplaced views are inspection findings, not acceptable omissions.
