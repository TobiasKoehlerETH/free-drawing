# Regression suite results — 2026-10-18

Both existing suites completed successfully: **134 passed, 0 failed, 0 skipped** across two complementary pytest selections. These results confirm the covered behaviors; they are not a claim that every generated drawing is dimensionally complete or ready for manufacture.

## Commands and outcomes

Commands ran from `C:/Code/free-drawing`, with pytest running cases serially. The broad sample runner was active independently, so durations include shared-machine load.

| Command | Result | Runtime |
|---|---|---:|
| `uv run --group dev pytest -q -m "not freecad"` | 127 passed, 7 deselected; exit 0 | 12.63 s |
| `uv run --group dev pytest -q -m freecad --durations=10 --junitxml=docs/test-suite-freecad-2026-10-18.xml` | 7 passed, 127 deselected; exit 0 | 190.10 s |

Machine-readable evidence: [FreeCAD JUnit report](test-suite-freecad-2026-10-18.xml). This run began at `2026-10-18T11:06:14+02:00`.

Environment: Windows, Python 3.11.15, detected backend `C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe`. Repository HEAD was `db4c068d391009f18605d3bf49ce7e14aefeca51` with local working changes.

## Real geometry coverage

The seven tests cover **five distinct STEP sources and six created drawings**, using thirteen fresh FreeCAD backend processes. The two integration tests share one drawing; counterbored block runs in two projection methods.

| Source / test | Checks verified | Runtime including fixture setup |
|---|---|---:|
| `boss_plate` creation | Editable FCStd exists; expected orthographic/pictorial views and scale; E.1, E.10, E.11 do not fail; missing material is reported | 15.062 s |
| `boss_plate` actions/render/export | Material persists after applying action; K.1 changes to pass; 1600 px PNG dimensions, CRCs and decompression; nonempty PDF with correct signature | 10.062 s |
| `counterbored_block`, first-angle | Saved section reopens; valid parent, visible geometry, finite bounds, material hatch, cutting arrows; section dimensions; hidden dimension warning resolved | 14.274 s |
| `counterbored_block`, third-angle | Same persistence and section assertions with third-angle projection | 12.342 s |
| `tube`, first-angle | Saved section, parent link, material hatch and cutting arrows survive reopening | 69.858 s |
| `micro_grooves`, first-angle | Saved enlarged detail reopens with valid parent, visible geometry, greater scale, and parent reference circle | 29.710 s |
| `flange`, first-angle | Saved section reopens; both 90 mm pitch diameter and 120 mm outside diameter are retained | 28.420 s |

All supplementary cases also check unique section/detail labels, absence of geometry errors, and no failing D.1, D.2, E.1 or E.11 findings.

## Revision limitation

Production files changed concurrently during this test run. The passing results therefore represent execution during active development, **not one frozen final revision**. No production files were edited by the regression-test worker.

| File | SHA-256 near start | SHA-256 after completion |
|---|---|---|
| `addon/FreeDrawingMCP/fd_drawing/builder.py` | `95764CA92B763812B1CEC6870C2127E3F4406BD3EBA4717B2D8919813E6FB65E` | `31C0005EAAE8525B0257BC19B0E8E701EDF5610585BD5B3A5BDB0C86F2D409A0` |
| `src/free_drawing_mcp/rules/checks.py` | `1257AF325B248A20BB773BEB6D790780074D0D6653E62E4228A5A61D29798D2E` | `24E00C6C934B3158C9AA0E6FD27EEC68253D3F94441E0F9193B8CD2368CCBD87` |

A final-revision qualification requires rerunning relevant suites after edits settle. The much broader complex-part corpus and visual inspection results belong to the separate stress-test report; the existing seven FreeCAD tests alone do not establish broad shape coverage or final sheet quality.
