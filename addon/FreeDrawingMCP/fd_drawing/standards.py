"""Drafting defaults taken from ``guidelines/`` (ISO/DIN, metric, first-angle).

Everything here is plain data so the planner, the snapshot and the rules
engine agree on one set of numbers. Guideline file in brackets.
"""

# [00] ISO 5457 sheet sizes, landscape (width, height) in mm.
SHEET_SIZES = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}
DEFAULT_SHEET = "A3"
DEFAULT_PROJECTION = "first"  # [02] first-angle unless stated otherwise

# [01] ISO 128 line group 0.7: thick visible, thin everything else (1:2).
LINE_THICK = 0.7
LINE_THIN = 0.35
CENTER_LINE_STYLE = 4  # TechDraw line style index: dash-dot (ISO 128 type G)

# ISO 5455 preferred scales. Project override [README]: orthographic views stay
# 1:1 unless they do not fit, so only reductions are tried by default.
REDUCTION_SCALES = [1.0, 1 / 2, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100]
ENLARGEMENT_SCALES = [2.0, 5.0, 10.0]
ISO_VIEW_SCALE = 0.5  # project override: compact isometric at 1:2

# [04] dimension spacing (ISO 129): first line >= 10 mm off the outline,
# following parallel lines ~7-8 mm apart.
DIM_FIRST_OFFSET = 10.0
DIM_STEP = 8.0
DIM_FONT_SIZE = 3.5
VIEW_GAP = 12.0  # free space between neighbouring views, beyond dimension stacks
SHEET_MARGIN = 6.0  # keep-out inside the drawing frame

# Default text blocks [00][05][10][14]
DEFAULT_GENERAL_TOLERANCE = ""
DEFAULT_NOTES = [
    "UNITS: mm",
    "TOLERANCING PRINCIPLE: ISO 8015",
]

# [11] revision letters that must not be used.
REVISION_SKIP_LETTERS = set("IOQSXZ")

# Template defaults shipped with FreeCAD ISO templates. Left unchanged they are
# placeholders, not real title-block content [00].
TEMPLATE_PLACEHOLDERS = {
    "A. Nemesis", "B. Hecate", "DN", "AAA", "ST1", "ST2", "RD",
    "Stainless steel Mat.No. 1.4301", "TBD", "", "-", "?", "XXX",
}

DIAMETER_SYMBOL = "⌀"  # TechDraw's symbol; guidelines write the same sign as Ø
