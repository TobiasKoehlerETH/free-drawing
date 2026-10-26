"""Validate the coloured isometric and dimensioned-view policy on varied parts.

    uv run python samples/validate_view_policy.py --out samples/validation/isometric-check

Each drawing is reopened in a fresh FreeCAD process before inspection and export.
Generation success, view-policy checks and engineering findings are separate results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("plate_hole_pattern", "parts", "hole-pattern plate"),
    ("flange", "parts", "flange and pitch circle"),
    ("turned_shaft", "parts", "turned shaft"),
    ("tube", "parts", "hollow cylindrical part"),
    ("counterbored_block", "parts", "counterbores and section"),
    ("sheet_tray", "parts", "bent sheet metal"),
    ("slotted_plate", "parts", "slots and radii"),
    ("tiny_pin", "parts", "small part requiring enlargement"),
    ("large_frame", "parts", "large part requiring reduction"),
    ("micro_grooves", "complex", "fine turned features"),
    ("rotated_manifold", "complex", "rotated complex solid"),
    ("mixed_assembly", "complex", "multiple components"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", type=Path, default=ROOT)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--names", nargs="+")
    args = parser.parse_args()
    out, engine = args.out.resolve(), args.engine.resolve()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if out.exists() and any(out.iterdir()):
        parser.error("choose a new or empty output directory")
    cases = [case for case in CASES if not args.names or case[0] in args.names]
    if not cases or (args.names and set(args.names) != {c[0] for c in cases}):
        parser.error("--names must match names in CASES")
    sys.path.insert(0, str(engine / "src"))
    out.mkdir(parents=True, exist_ok=True)
    before = code_hashes(engine)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(run_case, case, out) for case in cases]
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            results.sort(key=lambda row: row["part"])
            write_json(out / "results.json", {"engine": str(engine), "engine_unchanged": before == code_hashes(engine),
                                              "code_sha256": before, "cases": results})
            print(f"{result['part']}: {result['status']}; policy={result.get('policy_passed')}; "
                  f"layout findings={len(result.get('layout_findings', []))}", flush=True)
    write_report(results, out)
    return int(any(row["status"] != "ok" or not row.get("policy_passed") for row in results))


def run_case(case, out):
    from free_drawing_mcp.backends import HeadlessBackend
    from free_drawing_mcp.rules import inspect
    name, folder, family = case
    source = ROOT / "samples" / folder / f"{name}.step"
    target = out / f"{name}.FCStd"
    result = {"part": name, "family": family, "source": str(source)}
    try:
        result["source_sha256"] = digest(source)
        backend = HeadlessBackend(timeout=300)
        created, initial = backend.run([
            {"op": "create_drawing", "args": {"source": str(source), "output": str(target), "title": name}},
            {"op": "snapshot", "args": {"doc": str(target)}},
        ])
        saved_hash = digest(target)
        snap, pdf, png = backend.run([
            {"op": "snapshot", "args": {"doc": str(target)}},
            {"op": "export", "args": {"doc": str(target), "format": "pdf", "path": str(out / f"{name}.pdf")}},
            {"op": "render", "args": {"doc": str(target), "width": 1800, "out_path": str(out / f"{name}.png")}},
        ])
        write_json(out / f"{name}.snapshot.json", snap)
        write_json(out / f"{name}.created.json", created)
        report = inspect(snap)
        write_json(out / f"{name}.report.json", report.to_dict(include_pass=True))
        checks = check_policy(snap)
        checks.update(dimensions_survive_reopen=dimension_state(initial) == dimension_state(snap),
                      drawing_unchanged_on_reopen=saved_hash == digest(target),
                      source_unchanged=result["source_sha256"] == digest(source),
                      pdf_header=(out / f"{name}.pdf").read_bytes().startswith(b"%PDF-"),
                      png_header=(out / f"{name}.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        findings = [{"rule": f.rule, "status": f.status, "message": f.message}
                    for f in report.findings if f.status in ("fail", "warn")]
        result.update(status="ok", policy_passed=all(checks.values()), checks=checks,
                      scale=created["scale"], dimensions=len(snap["dimensions"]),
                      sections=len(created["sections"]), details=len(created["details"]),
                      layout_findings=[f for f in findings if f["rule"] in ("A.8", "C.2", "C.3", "C.7", "E.10", "E.11")],
                      other_findings=[f for f in findings if f["rule"] not in ("A.8", "C.2", "C.3", "C.7", "E.10", "E.11")],
                      unplaced=created.get("unplaced", []), pdf_source=pdf["source"])
    except Exception as error:
        result.update(status="error", policy_passed=False, error=f"{type(error).__name__}: {error}")
    write_json(out / f"{name}.result.json", result)
    return result


def check_policy(snap):
    from free_drawing_mcp.rules import geom
    views = [v for v in snap["views"] if v["kind"] != "group"]
    isos = [v for v in views if v["kind"] == "pictorial"]
    defining = [v for v in views if v["kind"] != "pictorial"]
    attached = {d.get("view") for d in snap["dimensions"]}
    iso = isos[0] if len(isos) == 1 else {}
    box = iso.get("bbox")
    sheet = snap["sheet"]
    title = sheet["title_block"]
    clear = bool(box)
    if box:
        clear = not any(geom.overlap(box, v.get("bbox")) for v in defining)
        clear = clear and not any(geom.overlap(box, d.get("text_box")) or
                                  any(geom.seg_box(line, box) for line in d.get("ink_lines", d.get("lines", [])))
                                  for d in snap["dimensions"])
        clear = clear and not any(geom.overlap(box, a.get("bbox")) for a in snap["annotations"])
    return {
        "one_coloured_isometric": len(isos) == 1 and bool(iso.get("shaded")),
        "isometric_bottom_right": bool(box and (box[0] + box[2]) / 2 > sheet["width"] * 0.55
                                        and box[1] >= title[3] and (box[1] + box[3]) / 2 < sheet["height"] * 0.6),
        "isometric_clear_of_views_text_and_lines": clear,
        "isometric_has_no_dimensions": bool(iso) and iso["name"] not in attached,
        "every_defining_view_has_dimensions": bool(defining) and all(v["name"] in attached for v in defining),
        "standard_views_share_scale": len({v["scale"] for v in defining if v["kind"] == "ortho"}) == 1,
        "minimal_supplementary_views": sum(v["kind"] == "section" for v in defining) <= 1
                                       and sum(v["kind"] == "detail" for v in defining) <= 1,
    }


def dimension_state(snap):
    return {d["name"]: (d.get("view"), d.get("value"), d.get("refs")) for d in snap["dimensions"]}


def code_hashes(engine):
    return {str(p.relative_to(engine)): digest(p) for folder in (engine / "src", engine / "addon")
            for p in sorted(folder.rglob("*.py"))}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_report(results, out):
    lines = ["# Coloured isometric validation", "", "Generated with default drawing options and reopened in fresh FreeCAD processes.", "",
             "| Part | Family | View policy | Scale | Dimensions | Layout findings |", "| --- | --- | --- | --- | --- | --- |"]
    for row in results:
        lines.append(f"| [{row['part']}]({row['part']}.pdf) | {row['family']} | {'PASS' if row.get('policy_passed') else 'FAIL'} | "
                     f"{row.get('scale', '-')} | {row.get('dimensions', '-')} | {len(row.get('layout_findings', []))} |")
    lines += ["", "## Findings", ""]
    for row in results:
        failed = [key for key, value in row.get('checks', {}).items() if not value]
        lines.append(f"- **{row['part']}**: " + (row.get('error') or (', '.join(failed) if failed else 'view-policy and persistence checks pass')))
        for finding in row.get('layout_findings', []):
            lines.append(f"  - {finding['status'].upper()} {finding['rule']}: {finding['message']}")
        for finding in row.get('other_findings', []):
            if finding['rule'] not in ('F.1', 'K.1'):
                lines.append(f"  - {finding['status'].upper()} {finding['rule']}: {finding['message']}")
        if row.get('unplaced'):
            lines.append(f"  - {len(row['unplaced'])} unplaced requirements; see the JSON report.")
    lines += ["", "Material, tolerance, unsupported geometry and other engineering findings remain in each report. A view-policy pass is not manufacturing approval.", ""]
    (out / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
