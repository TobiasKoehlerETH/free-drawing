"""Run a frozen local drawing engine without MCP, RPC, or per-part decisions.

    uv run python samples/benchmark_offline.py --engine .scratch/frozen-engine \
        --source samples/parts --source samples/complex --out samples/benchmark

Each STEP runs in a separate FreeCADCmd process. A second process reopens the
saved drawing, exports PDF/PNG, and captures persistence evidence. No drawing
corrections run. All inputs share the same options.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    if args.jobs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("jobs and timeout must be positive")
    args.engine = args.engine.resolve()
    args.out = args.out.resolve()
    if not (args.engine / "src" / "free_drawing_mcp" / "backends.py").is_file():
        parser.error("engine must contain src/free_drawing_mcp/backends.py")
    if args.out.exists() and not args.out.is_dir():
        parser.error("output path must be a directory")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output directory must be new or empty")
    sys.path.insert(0, str(args.engine / "src"))
    from free_drawing_mcp import backends, rules
    if backends.ADDON_DIR.resolve() != args.engine / "addon" / "FreeDrawingMCP":
        parser.error("backend did not resolve to the frozen engine")
    if not backends.HeadlessBackend().available():
        parser.error("FreeCADCmd was not found")
    sources = []
    for source in args.source:
        if source.is_dir():
            sources.extend(p.resolve() for p in source.iterdir()
                           if p.is_file() and p.suffix.lower() in (".step", ".stp"))
        elif source.is_file() and source.suffix.lower() in (".step", ".stp"):
            sources.append(source.resolve())
        else:
            parser.error(f"source must be an existing STEP file or directory: {source}")
    sources = sorted(set(sources))
    names = [source.stem.casefold() for source in sources]
    if not sources or len(names) != len(set(names)):
        parser.error("select at least one source; source stems must be unique")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "engine": str(args.engine), "code_before": engine_hashes(args.engine),
        "protocol": "one-shot local FreeCADCmd; no network, MCP, RPC, AI or corrections",
        "options": {"views": "auto (minimum Front, Top, Right)", "template": "A3",
                    "projection": "first", "scale": "auto", "sections": True, "details": True},
        "timeout_seconds": args.timeout, "jobs": args.jobs,
        "inputs": [{"source": str(p), "sha256": sha256(p)} for p in sources],
    }
    write_json(args.out / "manifest.json", manifest)
    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, p, args, backends, rules) for p in sources]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            write_json(args.out / "summary.json", sorted(results, key=lambda r: r["part"]))
            print(f"{result['part']}: {result['status']} ({result['seconds']}s)", flush=True)
    manifest["wall_seconds"] = round(time.monotonic() - started, 1)
    manifest["code_after"] = engine_hashes(args.engine)
    manifest["engine_unchanged"] = manifest["code_before"] == manifest["code_after"]
    manifest["inputs_unchanged"] = all(Path(row["source"]).is_file()
                                       and sha256(Path(row["source"])) == row["sha256"]
                                       for row in manifest["inputs"])
    write_json(args.out / "manifest.json", manifest)
    return int(not manifest["engine_unchanged"] or not manifest["inputs_unchanged"]
               or any(r["status"] != "ok" for r in results))


def run_case(source, args, backends, rules):
    started = time.monotonic()
    output = args.out / source.stem
    # with_suffix() would truncate dotted stems (part.revA -> part.FCStd),
    # allowing otherwise unique source names to overwrite one another.
    def artifact(suffix):
        return output.parent / (output.name + suffix)
    drawing = artifact(".FCStd")
    pdf_path, png_path = artifact(".pdf"), artifact(".png")
    result = {"part": source.stem, "source": str(source)}
    backend = backends.HeadlessBackend(timeout=args.timeout)
    try:
        result["source_sha256"] = sha256(source)
        created, initial = backend.run([
            {"op": "create_drawing", "args": {"source": str(source), "output": str(drawing),
              "views": "auto", "template": "A3", "projection": "first", "scale": "auto",
              "sections": True, "details": True, "title_block": {"date_of_issue": "2026-10-18"}}},
            {"op": "snapshot", "args": {"doc": str(drawing), "include_geometry": True}},
        ])
        result["generation_seconds"] = round(time.monotonic() - started, 1)
        write_json(artifact(".initial.json"), initial)
        before = sha256(drawing)
        reopened, pdf, _ = backend.run([
            {"op": "snapshot", "args": {"doc": str(drawing), "include_geometry": True}},
            {"op": "export", "args": {"doc": str(drawing), "format": "pdf",
                                       "source": "schematic", "path": str(pdf_path)}},
            {"op": "render", "args": {"doc": str(drawing), "source": "schematic",
                                       "out_path": str(png_path), "width": 2400}},
        ])
        write_json(artifact(".snapshot.json"), reopened)
        report = rules.inspect(reopened)
        write_json(artifact(".report.json"), report.to_dict(include_pass=True))
        artifact(".report.txt").write_text(report.text(), encoding="utf-8")
        principal = [v for v in reopened["views"] if v.get("kind") == "ortho"]
        principal_names = [v.get("projection_type") for v in principal]
        remaining = [f for f in report.findings if f.status == "fail" and f.rule not in ("F.1", "K.1")]
        result.update(status="ok", created=created, inspection=report.summary(),
                      principal_views=principal_names,
                      three_views={"Front", "Top", "Right"}.issubset(principal_names),
                      saved_file_unchanged=before == sha256(drawing),
                      persistence_same=semantic_snapshot(initial) == semantic_snapshot(reopened),
                      semantic_sha256=hashlib.sha256(json.dumps(semantic_snapshot(reopened), sort_keys=True).encode()).hexdigest(),
                      engineering_failures=[{"rule": f.rule, "message": f.message} for f in remaining],
                      pdf_source=pdf["source"])
        result["pdf_valid_header"] = pdf_path.is_file() and pdf_path.read_bytes().startswith(b"%PDF-")
        result["png_valid_header"] = png_path.is_file() and png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        result["validation_failures"] = [key for key in
            ("three_views", "saved_file_unchanged", "persistence_same", "pdf_valid_header", "png_valid_header")
            if not result[key]]
        if result["validation_failures"]:
            result["status"] = "validation_failed"
    except Exception:
        result.update(status="error", error=traceback.format_exc())
    result["seconds"] = round(time.monotonic() - started, 1)
    write_json(artifact(".result.json"), result)
    return result


def semantic_snapshot(snapshot):
    volatile = {"document", "file", "image_file", "tag", "template_path"}
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in volatile}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, float):
            return round(value, 6)
        return value
    return clean({key: snapshot.get(key) for key in
                  ("units", "sheet", "title_block", "model", "views", "dimensions", "annotations", "meta", "projection")})


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engine_hashes(root):
    # Templates and bundled resources affect the sheet just as Python does.
    # Ignore only runtime bytecode, which imports may create in the frozen copy.
    return {str(p.relative_to(root)): sha256(p) for folder in ("src", "addon")
            for p in sorted((root / folder).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix.lower() not in (".pyc", ".pyo")}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
