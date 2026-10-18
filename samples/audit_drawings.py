"""Read-only persistence audit for drawings created by ``run_samples.py``.

    python samples/audit_drawings.py samples/out --out samples/audit --resume
    python samples/audit_drawings.py samples/out --names flange tube --pdf
    python samples/audit_drawings.py samples/repeat --compare samples/out

Only complete FCStd/snapshot pairs are selected. Each drawing is reopened by
HeadlessBackend in a fresh FreeCADCmd process. No fixes or save operations run.
The optional PDF uses the schematic headless renderer, without highlights.
Inspection findings, persistence checks, and missing design inputs are reported
separately; successful reopening does not establish drawing release readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AUDIT_VERSION = 1
EXPECTED_INPUT_FAILURES = {
    "F.1": "tolerance scheme needs design input",
    "K.1": "material not specified",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folders", nargs="+", type=Path, help="folders containing FCStd/snapshot pairs")
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "audit")
    parser.add_argument("--names", nargs="+", help="exact part stems to include")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120, help="seconds per fresh FreeCADCmd process")
    parser.add_argument("--resume", action="store_true", help="reuse successful audits of unchanged inputs")
    parser.add_argument("--pdf", action="store_true", help="export clean schematic PDFs for selected cases")
    parser.add_argument("--compare", type=Path, help="compare semantic snapshots with matching stems in this folder")
    parser.add_argument("--engine-root", type=Path, default=ROOT, help="repository or frozen copy containing src/ and addon/")
    args = parser.parse_args()
    if args.jobs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--jobs and --timeout must be positive")
    args.out = args.out.resolve()
    folders = list(dict.fromkeys(folder.resolve() for folder in args.folders))
    if any(not folder.is_dir() for folder in folders):
        parser.error("every input folder must exist")
    if args.out in folders:
        parser.error("--out must differ from every input folder")
    if args.compare and not args.compare.is_dir():
        parser.error("--compare must name an existing snapshot folder")
    args.engine_root = args.engine_root.resolve()
    try:
        args.engine_sha256 = configure_engine(args.engine_root)
    except (ValueError, ImportError) as error:
        parser.error(str(error))
    args.out.mkdir(parents=True, exist_ok=True)
    cases, skipped = discover_cases(folders, set(args.names or []), args.out)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(audit_case, case, args): case for case in cases}
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            print(f"{result['case']}: {result['status']} ({result['seconds']:.1f}s)", flush=True)
    results.sort(key=lambda result: result["case"])
    summary = summarize(results, skipped, folders)
    write_json(args.out / "summary.json", summary)
    markdown = summary_markdown(summary)
    (args.out / "summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    repeat_changed = any(result.get("repeatability", {}).get("same") is False for result in results)
    return 1 if any(result["status"] != "ok" for result in results) or not results or repeat_changed else 0


def configure_engine(engine_root: Path) -> str:
    """Load both the backend and inspector from one selected engine tree."""
    source = engine_root / "src"
    addon = engine_root / "addon" / "FreeDrawingMCP"
    if not (source / "free_drawing_mcp" / "backends.py").is_file() or not (addon / "fd_drawing" / "runner.py").is_file():
        raise ValueError("--engine-root must contain src/free_drawing_mcp/backends.py and addon/FreeDrawingMCP/fd_drawing/runner.py")
    for name in list(sys.modules):
        if name == "free_drawing_mcp" or name.startswith("free_drawing_mcp."):
            del sys.modules[name]
    sys.path.insert(0, str(source))
    from free_drawing_mcp import backends, rules

    backends.ADDON_DIR = addon
    global HeadlessBackend, inspect
    HeadlessBackend = backends.HeadlessBackend
    inspect = rules.inspect
    digest = hashlib.sha256()
    for path in sorted(list((source / "free_drawing_mcp").rglob("*.py")) + list((addon / "fd_drawing").rglob("*.py"))):
        digest.update(path.relative_to(engine_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def discover_cases(folders: list[Path], names: set[str], out: Path) -> tuple[list[dict], list[dict]]:
    cases, skipped = [], []
    labels = Counter(folder.name.casefold() for folder in folders)
    for folder in folders:
        label = folder.name
        if labels[label.casefold()] > 1:
            label += "-" + hashlib.sha256(str(folder).encode()).hexdigest()[:8]
        for fcstd in sorted(folder.iterdir()):
            if fcstd.suffix.lower() != ".fcstd" or (names and fcstd.stem not in names):
                continue
            snapshot = fcstd.with_suffix(".snapshot.json")
            case = {"case": f"{label}/{fcstd.stem}", "part": fcstd.stem, "fcstd": str(fcstd),
                    "original_snapshot": str(snapshot), "out": str(out / label)}
            if not snapshot.is_file() or not fcstd.stat().st_size:
                skipped.append({"case": case["case"], "reason": "FCStd/snapshot pair is not ready"})
                continue
            try:
                json.loads(snapshot.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                skipped.append({"case": case["case"], "reason": f"snapshot is not readable: {error}"})
                continue
            cases.append(case)
    return cases, skipped


def audit_case(case: dict, args: argparse.Namespace) -> dict:
    started = time.monotonic()
    out = Path(case["out"])
    out.mkdir(parents=True, exist_ok=True)
    prefix = out / case["part"]
    result_path = artifact_path(prefix, ".audit.json")
    result = {**case, "audit_version": AUDIT_VERSION, "engine_root": str(args.engine_root), "engine_sha256": args.engine_sha256,
              "status": "error", "checks": {}, "errors": []}
    stage = "read inputs"
    try:
        originals = [Path(case["fcstd"]), Path(case["original_snapshot"])]
        input_hashes = {str(path): file_hash(path) for path in originals}
        result["input_sha256"] = input_hashes
        if args.resume and result_path.is_file():
            previous = json.loads(result_path.read_text(encoding="utf-8"))
            if (previous.get("audit_version") == AUDIT_VERSION and previous.get("input_sha256") == input_hashes
                    and previous.get("engine_root") == str(args.engine_root)
                    and previous.get("engine_sha256") == args.engine_sha256
                    and previous.get("status") == "ok"
                    and (not args.pdf or Path(previous.get("pdf", {}).get("path", "")).is_file())
                    and all(Path(previous.get(key, "")).is_file()
                            for key in ("reopened_snapshot", "inspection_report"))):
                if args.compare:
                    previous["repeatability"] = compare_reference(json.loads(originals[1].read_text(encoding="utf-8")),
                                                                   args.compare, case["part"])
                    write_json(result_path, previous)
                previous["resumed"] = True
                return previous
        original = json.loads(originals[1].read_text(encoding="utf-8"))
        stage = "reopen snapshot"
        reopened = HeadlessBackend(timeout=args.timeout).run([
            {"op": "snapshot", "args": {"doc": case["fcstd"], "include_geometry": True}},
        ])[0]
        snapshot_path = artifact_path(prefix, ".reopened.snapshot.json")
        write_json(snapshot_path, reopened)
        result["reopened_snapshot"] = str(snapshot_path)
        result["checks"] = persistence_checks(original, reopened)
        result["original_semantic_sha256"] = semantic_fingerprint(original)
        result["reopened_semantic_sha256"] = semantic_fingerprint(reopened)
        result["counts"] = {"views": len(reopened.get("views", [])),
                            "view_kinds": dict(Counter(view.get("kind") for view in reopened.get("views", []))),
                            "dimensions": len(reopened.get("dimensions", []))}
        stage = "inspect reopened snapshot"
        report = inspect(reopened).to_dict(include_pass=True)
        report_path = artifact_path(prefix, ".reopened.report.json")
        write_json(report_path, report)
        result["inspection_report"] = str(report_path)
        result["inspection"] = finding_summary(report)
        if args.compare:
            result["repeatability"] = compare_reference(original, args.compare, case["part"])
        if args.pdf:
            stage = "export schematic PDF"
            pdf_path = artifact_path(prefix, ".schematic.pdf")
            result["pdf"] = HeadlessBackend(timeout=args.timeout).run([
                {"op": "export", "args": {"doc": case["fcstd"], "path": str(pdf_path),
                                            "format": "pdf", "source": "schematic", "highlights": None}},
            ])[0]
            if not pdf_path.is_file() or pdf_path.read_bytes()[:5] != b"%PDF-":
                raise ValueError("export did not produce a PDF file")
        result["status"] = "ok" if all(check["ok"] for check in result["checks"].values()) else "check_failure"
    except Exception as error:
        result["errors"].append({"stage": stage, "type": type(error).__name__, "message": str(error),
                                 "traceback": traceback.format_exc()})
    finally:
        if result.get("input_sha256"):
            try:
                changed = [path for path, digest in result["input_sha256"].items() if file_hash(Path(path)) != digest]
                result["checks"]["original_files_unchanged"] = check(changed)
                if changed:
                    result["status"] = "check_failure"
            except Exception as error:
                result["errors"].append({"stage": "verify original files", "type": type(error).__name__,
                                         "message": str(error), "traceback": traceback.format_exc()})
                result["status"] = "error"
        result["seconds"] = round(time.monotonic() - started, 2)
    write_json(result_path, result)
    return result


def persistence_checks(original: dict, reopened: dict) -> dict:
    before_dims = {dim["name"]: dim for dim in original.get("dimensions", [])}
    after_dims = {dim["name"]: dim for dim in reopened.get("dimensions", [])}
    before_views = {view["name"]: view for view in original.get("views", [])}
    after_views = {view["name"]: view for view in reopened.get("views", [])}
    measurements, references, invalid, parents, geometry_errors = [], [], [], [], []
    for name in sorted(before_dims.keys() & after_dims.keys()):
        before, after = before_dims[name], after_dims[name]
        a, b = before.get("value"), after.get("value")
        if not finite_number(a) or not finite_number(b) or not math.isclose(a, b, rel_tol=1e-9, abs_tol=0.005):
            measurements.append({"name": name, "before": a, "after": b})
        for field in ("type", "view", "refs", "arbitrary", "format_spec"):
            if before.get(field) != after.get(field):
                references.append({"name": name, "field": field, "before": before.get(field), "after": after.get(field)})
    for name, dim in after_dims.items():
        issues = []
        if dim.get("view") not in after_views:
            issues.append("missing parent view")
        if not dim.get("refs"):
            issues.append("no geometric references")
        if not finite_number(dim.get("value")):
            issues.append("missing or nonfinite measured value")
        elif dim["value"] == 0:
            issues.append("zero measured value requires review")
        if issues:
            invalid.append({"name": name, "issues": issues})
    for name, view in after_views.items():
        parent_field = "base_view" if view.get("kind") in ("section", "detail") else "group"
        if (parent_field == "base_view" or view.get(parent_field)) and view.get(parent_field) not in after_views:
            parents.append({"name": name, "field": parent_field, "parent": view.get(parent_field)})
        for field in ("section_error", "detail_error", "hatch_error"):
            if view.get(field):
                geometry_errors.append({"name": name, "field": field, "message": view[field]})
        if view.get("kind") in ("ortho", "section", "detail") and not view.get("edges_visible", 0):
            geometry_errors.append({"name": name, "message": "view has no visible edges"})
    return {
        "dimension_names": check(name_changes(before_dims, after_dims)),
        "dimension_values": check(measurements),
        "dimension_references": check(references),
        "dimension_validity": {**check(invalid), "scope": "Snapshot references and measurements; FreeCAD native Object.State is not exposed by this API."},
        "view_names": check(name_changes(before_views, after_views)),
        "view_kinds_and_edge_counts": check(record_changes(before_views, after_views, ("kind", "edges_visible", "edges_hidden"))),
        "parent_references": check(parents),
        "view_geometry": check(geometry_errors),
        "finite_geometry": check(nonfinite_paths(reopened)),
    }


def semantic_snapshot(snapshot: dict) -> dict:
    """Stable geometry/annotation data for repeat runs, excluding volatile paths and IDs."""
    view_fields = ("name", "kind", "type_id", "projection", "projection_type", "group", "base_view", "center",
                   "bbox", "scale", "direction", "x_direction", "axis", "edges_visible", "edges_hidden",
                   "hard_hidden", "smooth_visible", "section_symbol", "reference", "cutting_line", "detail_circle",
                   "hatch", "axis_line", "polylines_visible", "polylines_hidden", "section_error", "detail_error", "hatch_error")
    dimension_fields = ("name", "type", "view", "refs", "value", "text", "format_spec", "arbitrary", "role",
                        "label_xy", "text_box", "text_center", "lines", "tolerance", "theoretical_exact", "ref_hidden")
    meta = snapshot.get("meta") or {}
    return {"projection": snapshot.get("projection"), "units": snapshot.get("units"),
            "sheet": {key: snapshot.get("sheet", {}).get(key) for key in ("width", "height", "size_name", "orientation", "frame", "title_block")},
            "model": {key: snapshot.get("model", {}).get(key) for key in ("solids", "extents")},
            "views": selected_records(snapshot.get("views", []), view_fields),
            "dimensions": selected_records(snapshot.get("dimensions", []), dimension_fields),
            "annotations": selected_records(snapshot.get("annotations", []), ("name", "kind", "text", "bbox", "size")),
            "coverage": {key: meta.get(key) for key in ("unplaced", "coverage", "coverage_gaps", "supplementary_views")}}


def semantic_fingerprint(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(semantic_snapshot(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def compare_reference(snapshot: dict, folder: Path, part: str) -> dict:
    path = folder / f"{part}.snapshot.json"
    if not path.is_file():
        return {"available": False, "reference": str(path), "reason": "matching snapshot is missing"}
    reference = json.loads(path.read_text(encoding="utf-8"))
    before, after = semantic_snapshot(reference), semantic_snapshot(snapshot)
    changes = differing_paths(before, after)
    return {"available": True, "reference": str(path), "same": not changes,
            "reference_sha256": semantic_fingerprint(reference), "current_sha256": semantic_fingerprint(snapshot),
            "changed_paths": changes}


def finding_summary(report: dict) -> dict:
    expected, other, warnings, reviews = [], [], [], []
    for finding in report["findings"]:
        status = finding["status"]
        if status == "fail":
            target = expected if EXPECTED_INPUT_FAILURES.get(finding["rule"]) == finding["message"] else other
            target.append(finding)
        elif status == "warn":
            warnings.append(finding)
        elif status == "review":
            reviews.append(finding)
    return {"summary": report["summary"], "expected_input_failures": expected, "other_failures": other,
            "warnings": warnings, "reviews": reviews, "unplaced": report.get("unplaced", [])}


def summarize(results: list[dict], skipped: list[dict], folders: list[Path]) -> dict:
    inspected = [result for result in results if "inspection" in result]
    comparisons = [result["repeatability"] for result in results if "repeatability" in result]
    return {"audit_version": AUDIT_VERSION, "input_folders": [str(folder) for folder in folders],
            "scope": "Read-only reopen and snapshot checks; schematic PDF only when requested. No fixes. Native Object.State is not exposed by snapshots. Inspector findings remain unresolved.",
            "counts": {"selected": len(results), "reopened": sum("reopened_snapshot" in result for result in results),
                       "persistence_ok": sum(result["status"] == "ok" for result in results),
                       "persistence_check_failures": sum(result["status"] == "check_failure" for result in results),
                       "errors": sum(result["status"] == "error" for result in results), "skipped_incomplete": len(skipped),
                       "inspector_result": dict(Counter(result["inspection"]["summary"]["result"] for result in inspected)),
                       "expected_input_failures": sum(len(result["inspection"]["expected_input_failures"]) for result in inspected),
                       "other_failures": sum(len(result["inspection"]["other_failures"]) for result in inspected),
                       "warnings": sum(len(result["inspection"]["warnings"]) for result in inspected),
                       "reviews": sum(len(result["inspection"]["reviews"]) for result in inspected),
                       "repeatability": {"same": sum(item.get("same") is True for item in comparisons),
                                         "changed": sum(item.get("same") is False for item in comparisons),
                                         "missing_reference": sum(not item.get("available") for item in comparisons)}},
            "other_failure_rules": dict(Counter(finding["rule"] for result in inspected for finding in result["inspection"]["other_failures"])),
            "results": results, "skipped": skipped}


def summary_markdown(summary: dict) -> str:
    counts = summary["counts"]
    lines = ["# Saved drawing audit", "", summary["scope"], "",
             f"Selected {counts['selected']}; reopened {counts['reopened']}; persistence checks OK {counts['persistence_ok']}; "
             f"check failures {counts['persistence_check_failures']}; execution errors {counts['errors']}; incomplete pairs skipped {counts['skipped_incomplete']}.", "",
             f"Inspection findings: {counts['expected_input_failures']} expected missing material/tolerance inputs (F.1/K.1), "
             f"{counts['other_failures']} other failures, {counts['warnings']} warnings, {counts['reviews']} review items.", "",
             "Persistence success does not establish drawing completeness or manufacturing release readiness.", "",
             "| Case | Reopen checks | Views | Dims | Inspector | F.1/K.1 inputs | Other fails | Warn | Review |", "|---|---|---:|---:|---|---:|---:|---:|---:|"]
    for result in summary["results"]:
        inspection = result.get("inspection", {})
        lines.append(f"| {result['case']} | {result['status']} | {result.get('counts', {}).get('views', '-')} | "
                     f"{result.get('counts', {}).get('dimensions', '-')} | {inspection.get('summary', {}).get('result', '-')} | "
                     f"{len(inspection.get('expected_input_failures', []))} | {len(inspection.get('other_failures', []))} | "
                     f"{len(inspection.get('warnings', []))} | {len(inspection.get('reviews', []))} |")
    for result in summary["results"]:
        failed = [name for name, value in result["checks"].items() if not value["ok"]]
        if failed or result["errors"]:
            lines += ["", f"- **{result['case']}**: " + ", ".join(failed + [error["stage"] + ": " + error["message"].splitlines()[0] for error in result["errors"]])]
    repeat = counts["repeatability"]
    if sum(repeat.values()):
        lines += ["", f"Repeatability comparisons: {repeat['same']} identical semantic snapshots; "
                  f"{repeat['changed']} changed; {repeat['missing_reference']} matching reference snapshots missing."]
        for result in summary["results"]:
            if result.get("repeatability", {}).get("same") is False:
                lines.append(f"- **{result['case']}**: {len(result['repeatability']['changed_paths'])} semantic paths changed; see the per-case audit JSON.")
    return "\n".join(lines) + "\n"


def check(issues: list) -> dict:
    return {"ok": not issues, "issues": issues}


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def nonfinite_paths(value: Any, path: str = "snapshot") -> list[str]:
    if isinstance(value, dict):
        return [issue for key, child in value.items() for issue in nonfinite_paths(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [issue for index, child in enumerate(value) for issue in nonfinite_paths(child, f"{path}[{index}]")]
    return [path] if isinstance(value, float) and not math.isfinite(value) else []


def name_changes(before: dict, after: dict) -> list[dict]:
    return ([{"missing": name} for name in sorted(before.keys() - after.keys())]
            + [{"added": name} for name in sorted(after.keys() - before.keys())])


def record_changes(before: dict, after: dict, fields: tuple[str, ...]) -> list[dict]:
    return [{"name": name, "field": field, "before": before[name].get(field), "after": after[name].get(field)}
            for name in sorted(before.keys() & after.keys()) for field in fields
            if before[name].get(field) != after[name].get(field)]


def selected_records(records: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [{key: record.get(key) for key in fields} for record in sorted(records, key=lambda record: record.get("name", ""))]


def differing_paths(before: Any, after: Any, path: str = "snapshot") -> list[str]:
    if type(before) is not type(after):
        return [path]
    if isinstance(before, dict):
        return [issue for key in sorted(before.keys() | after.keys())
                for issue in ([f"{path}.{key}"] if key not in before or key not in after
                              else differing_paths(before[key], after[key], f"{path}.{key}"))]
    if isinstance(before, list):
        if len(before) != len(after):
            return [path + ".length"]
        return [issue for index, (a, b) in enumerate(zip(before, after))
                for issue in differing_paths(a, b, f"{path}[{index}]")]
    return [path] if before != after else []


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_path(prefix: Path, suffix: str) -> Path:
    return prefix.with_name(prefix.name + suffix)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
