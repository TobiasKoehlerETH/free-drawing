"""Compare existing benchmark snapshot pairs without running or modifying FreeCAD.

    python samples/analyze_persistence.py samples/validation-2026-10-18/final \
        --out samples/validation-2026-10-18/persistence-analysis

Full cosmetic circles are represented by their center, radius and format. Their
redundant tessellation is omitted only after validating closure, radial fit and
full angular traversal. All other semantic fields remain part of the comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from audit_drawings import persistence_checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results, pending, batches = [], [], []
    for folder in args.folders:
        manifest_path = folder / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                batches.append({"folder": str(folder), "expected_sources": len(manifest.get("inputs", [])),
                                "result_files": len(list(folder.glob("*.result.json"))),
                                "engine_unchanged": manifest.get("engine_unchanged"),
                                "inputs_unchanged": manifest.get("inputs_unchanged"),
                                "finished": "engine_unchanged" in manifest,
                                "unavailable_pairs": [Path(row["source"]).stem for row in manifest.get("inputs", [])
                                                      if not (folder / (Path(row["source"]).stem + ".initial.json")).is_file()
                                                      or not (folder / (Path(row["source"]).stem + ".snapshot.json")).is_file()]})
            except (OSError, ValueError):
                batches.append({"folder": str(folder), "finished": False, "manifest_readable": False})
        for initial_path in sorted(folder.glob("*.initial.json")):
            part = initial_path.name.removesuffix(".initial.json")
            snapshot_path = folder / (part + ".snapshot.json")
            if not snapshot_path.is_file():
                pending.append(str(initial_path))
                continue
            try:
                initial = json.loads(initial_path.read_text(encoding="utf-8"))
                reopened = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pending.append(str(initial_path))
                continue
            results.append(analyze_pair(initial, reopened, str(folder), part))
    summary = {"scope": "Existing initial/reopened snapshots only; no generation, fixes or FreeCAD process.",
               "normalization": "Benchmark volatile-field exclusions and 6-decimal rounding; validated full cosmetic circles omit redundant points, retaining center/radius/format/edge/kind.",
               "counts": {"pairs": len(results), "raw_same": sum(result["raw_same"] for result in results),
                          "canonical_same": sum(result["canonical_same"] for result in results),
                          "incidental_circle_parameterization": sum(not result["raw_same"] and result["canonical_same"] for result in results),
                          "genuine_semantic_changes": sum(not result["canonical_same"] for result in results),
                          "persistence_checks_ok": sum(result["checks_ok"] for result in results), "pending_pairs": len(pending)},
               "batches": batches, "results": results, "pending": pending}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Reopened drawing persistence", "", summary["scope"], "", summary["normalization"], "",
             json.dumps(summary["counts"], indent=2), "",
             "| Case | Persistence checks | Raw semantic equality | Canonical equality | Changed circles |", "|---|---|---|---|---:|"]
    for result in results:
        lines.append(f"| {Path(result['folder']).name}/{result['part']} | {result['checks_ok']} | {result['raw_same']} | {result['canonical_same']} | {len(result['changed_circles'])} |")
    for batch in batches:
        if batch.get("unavailable_pairs"):
            lines += ["", f"{batch['folder']}: unavailable snapshot pairs: {', '.join(batch['unavailable_pairs'])}; batch finished: {batch['finished']}."]
    for result in results:
        if result["raw_same"]:
            continue
        lines += ["", f"**{result['part']}**"]
        for circle in result["changed_circles"]:
            lines.append(f"- {circle['view']}.{circle['edge']}: circle center {circle['center']}, radius {circle['radius']} mm; "
                         f"only tessellation points differ: {circle['only_points_differ']}; exact traversal reversal: {circle['exact_reversed_points']}.")
        for check_name, check_result in result["checks"].items():
            if not check_result["ok"]:
                lines.append(f"- {check_name}: {json.dumps(check_result['issues'], ensure_ascii=False)}")
        if result["genuine_changes"]:
            lines.append(f"- {len(result['genuine_changes'])} genuine changed values remain; exact paths and before/after values are recorded in summary.json.")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary["counts"], indent=2))
    for result in results:
        if not result["raw_same"]:
            print(result["part"], "canonical_same=", result["canonical_same"], "circle_changes=", len(result["changed_circles"]))
    return int(any(not result["canonical_same"] or not result["checks_ok"] for result in results))


def analyze_pair(initial: dict, reopened: dict, folder: str, part: str) -> dict:
    before, after = benchmark_semantics(initial), benchmark_semantics(reopened)
    canonical_before, canonical_after = canonical_semantics(initial), canonical_semantics(reopened)
    checks = persistence_checks(initial, reopened)
    changed_circles = []
    for before_view, after_view in zip(before.get("views", []), after.get("views", [])):
        for index, (before_circle, after_circle) in enumerate(zip(before_view.get("cosmetic", []), after_view.get("cosmetic", []))):
            if before_circle.get("geometry") != "circle" or before_circle.get("points") == after_circle.get("points"):
                continue
            before_points = point_pairs(before_circle.get("points", []))
            after_points = point_pairs(after_circle.get("points", []))
            changed_circles.append({"view": before_view["name"], "cosmetic_index": index,
                                    "edge": before_circle.get("edge"), "center": before_circle.get("center"),
                                    "radius": before_circle.get("radius"),
                                    "before_valid_full_circle": full_circle(before_circle), "after_valid_full_circle": full_circle(after_circle),
                                    "exact_reversed_points": before_points == after_points[::-1],
                                    "same_point_set": sorted(before_points) == sorted(after_points),
                                    "only_points_differ": {key: value for key, value in before_circle.items() if key != "points"}
                                                          == {key: value for key, value in after_circle.items() if key != "points"}})
    return {"folder": folder, "part": part, "raw_same": before == after,
            "canonical_same": canonical_before == canonical_after,
            "canonical_before_sha256": fingerprint(canonical_before), "canonical_after_sha256": fingerprint(canonical_after),
            "checks_ok": all(item["ok"] for item in checks.values()), "checks": checks,
            "changed_circles": changed_circles, "raw_changes": changes(before, after),
            "genuine_changes": changes(canonical_before, canonical_after)}


def benchmark_semantics(snapshot: dict) -> dict:
    return clean({key: snapshot.get(key) for key in
                  ("units", "sheet", "title_block", "model", "views", "dimensions", "annotations", "meta", "projection")})


def canonical_semantics(snapshot: dict) -> dict:
    semantic = benchmark_semantics(snapshot)
    for view in semantic.get("views", []):
        for cosmetic in view.get("cosmetic", []):
            if full_circle(cosmetic):
                cosmetic.pop("points", None)
    return semantic


def full_circle(record: dict) -> bool:
    if record.get("geometry") != "circle" or not record.get("center") or not record.get("radius", 0) > 0:
        return False
    points = point_pairs(record.get("points", []))
    if len(points) < 12 or math.dist(points[0], points[-1]) > 0.02:
        return False
    center, radius = record["center"], record["radius"]
    if any(not all(math.isfinite(value) for value in point) or abs(math.dist(point, center) - radius) > 0.02 for point in points):
        return False
    angles = [math.atan2(y - center[1], x - center[0]) for x, y in points]
    sweep = sum(math.atan2(math.sin(b - a), math.cos(b - a)) for a, b in zip(angles, angles[1:]))
    return abs(abs(sweep) - 2 * math.pi) < 0.01


def point_pairs(points: list) -> list:
    return list(zip(points[::2], points[1::2]))


def clean(value):
    if isinstance(value, dict):
        volatile = {"document", "file", "image_file", "tag", "template_path"}
        return {key: clean(item) for key, item in value.items() if key not in volatile}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def changes(before, after, path="snapshot") -> list:
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(before.keys() | after.keys())
                for change in ([{"path": f"{path}.{key}", "before_present": key in before, "after_present": key in after,
                                 "before": before.get(key), "after": after.get(key)}]
                               if key not in before or key not in after else changes(before[key], after[key], f"{path}.{key}"))]
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [{"path": path, "before_length": len(before), "after_length": len(after)}]
        return [change for index, (a, b) in enumerate(zip(before, after)) for change in changes(a, b, f"{path}[{index}]")]
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
