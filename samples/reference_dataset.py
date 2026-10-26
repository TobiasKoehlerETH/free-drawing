"""Fetch pinned drawing references and evaluate saved drawings on paired STEP inputs.

    uv run python samples/reference_dataset.py fetch --out .scratch/drawing-references
    uv run python samples/reference_dataset.py evaluate --data .scratch/drawing-references --out samples/validation/references

Downloaded assets remain separate from this repository's MIT-licensed code.
Cadsy drawings are synthetic comparison material, not manufacturing ground truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CADSY_REVISION = "01a38048c5eaf09b777d7929df8e3c8a03e36965"
EDOCR_REVISION = "1786f898e0278fcc4574ce84a23ca6bad0c170e7"
CADSY = f"https://huggingface.co/datasets/cadsy/cad-technical-drawings/resolve/{CADSY_REVISION}"
EDOCR = f"https://raw.githubusercontent.com/javvi51/eDOCr/{EDOCR_REVISION}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "evaluate"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--engine", type=Path, default=ROOT)
    parser.add_argument("--parts", nargs="+", default=["part_0001", "part_0013", "part_0026", "part_0039", "part_0052", "part_0065"])
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.out, args.parts)
        return 0
    if not args.data:
        parser.error("evaluate requires --data pointing to the fetched references")
    return evaluate(args.data.resolve(), args.out.resolve(), args.engine.resolve())


def fetch(out, parts):
    records = []
    for filename in ("README.md", "LICENSE", "metadata.jsonl"):
        records.append(download(f"{CADSY}/{filename}", out / "cadsy" / filename))
    rows = [json.loads(line) for line in (out / "cadsy/metadata.jsonl").read_text().splitlines()]
    selected = [row for row in rows if row["part_id"] in parts]
    if set(parts) != {row["part_id"] for row in selected}:
        raise ValueError("--parts must name existing Cadsy part IDs")
    # Evenly spaced IDs are fixed before generation; failures are kept in results.
    files = [(f"{CADSY}/{row[key]}", out / "cadsy" / row[key])
             for row in selected for key in ("model_step", "iso_image", "iso_labels")]
    files += [(f"{EDOCR}/{name}", out / "edocr" / name) for name in
              ("README.md", "LICENSE", "tests/test_samples/Adapterplatte.jpg",
               "tests/test_samples/Gripper.jpg", "tests/test_samples/halter.jpg")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        records.extend(pool.map(lambda pair: download(*pair), files))
    manifest = {
        "sources": [
            {"id": "cadsy", "url": "https://huggingface.co/datasets/cadsy/cad-technical-drawings",
             "revision": CADSY_REVISION, "license": "Apache-2.0", "synthetic": True,
             "use": "paired geometry and annotation comparison; not certified drawings"},
            {"id": "edocr", "url": "https://github.com/javvi51/eDOCr",
             "revision": EDOCR_REVISION, "repository_license": "MIT",
             "use": "visual reference only; original drawing rights are not independently verified"}],
        "selection": "six evenly spaced IDs from 65 entries, selected before generation",
        "parts": selected, "files": records,
    }
    write_json(out / "manifest.json", manifest)
    print(f"Fetched {len(selected)} paired parts and three eDOCr reference sheets into {out}")


def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with urlopen(Request(url, headers={"User-Agent": "free-drawing-reference-evaluation"}), timeout=60) as response:
            data = response.read()
        if path.suffix == ".step" and not data.lstrip().startswith(b"ISO-10303-21;"):
            raise ValueError(f"Expected STEP content from {url}")
        if path.suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Expected PNG content from {url}")
        path.write_bytes(data)
    data = path.read_bytes()
    return {"url": url, "path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def evaluate(data, out, engine):
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new or empty output directory to preserve previous evidence")
    manifest = json.loads((data / "manifest.json").read_text())
    for record in manifest["files"]:
        if hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Reference file changed: {record['path']}")
    sys.path.insert(0, str(engine / "src"))
    from free_drawing_mcp.backends import HeadlessBackend
    from free_drawing_mcp.rules import inspect

    out.mkdir(parents=True, exist_ok=True)
    code_before = engine_hashes(engine)
    results = []
    for part in manifest["parts"]:
        name = part["part_id"]
        target = out / f"{name}.FCStd"
        row = {"part": name, "source": part["model_step"]}
        try:
            backend = HeadlessBackend(timeout=300)
            created, initial = backend.run([
                {"op": "create_drawing", "args": {"source": str(data / "cadsy" / part["model_step"]), "output": str(target)}},
                {"op": "snapshot", "args": {"doc": str(target)}},
            ])
            snap, pdf, png = backend.run([
                {"op": "snapshot", "args": {"doc": str(target)}},
                {"op": "export", "args": {"doc": str(target), "format": "pdf", "path": str(out / f"{name}.pdf")}},
                {"op": "export", "args": {"doc": str(target), "format": "png", "path": str(out / f"{name}.png")}},
            ])
            report = inspect(snap)
            labels = json.loads((data / "cadsy" / part["iso_labels"]).read_text())
            row.update(status="ok", **metrics(snap),
                       persisted_dimensions=dimension_values(initial) == dimension_values(snap),
                       reference_dimensions_by_view=dict(Counter(a["view"] for a in labels["annotations"]
                                                                if a["type"] == "dimension")),
                       findings=[{"rule": f.rule, "status": f.status, "message": f.message}
                                 for f in report.findings if f.status in ("fail", "warn")],
                       export_source=pdf["source"])
            write_json(out / f"{name}.snapshot.json", snap)
            write_json(out / f"{name}.created.json", created)
        except Exception as error:
            row.update(status="error", error=f"{type(error).__name__}: {error}")
        results.append(row)
        write_json(out / "results.json", {"engine": str(engine), "dataset_revision": CADSY_REVISION,
                                          "code_sha256": code_before, "engine_unchanged": code_before == engine_hashes(engine),
                                          "cases": results})
        print(json.dumps({k: v for k, v in row.items() if k not in ("findings", "error")}), flush=True)
    return int(any(row["status"] != "ok" or not row.get("persisted_dimensions") for row in results))


def engine_hashes(engine):
    return {str(path.relative_to(engine)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in (engine / "src", engine / "addon") for path in sorted(folder.rglob("*.py"))}


def metrics(snap):
    views = [v for v in snap["views"] if v["kind"] != "group"]
    counts = Counter(d.get("view") for d in snap["dimensions"])
    return {"views": len(views), "dimensions": len(snap["dimensions"]),
            "standard_scales": sorted({v["scale"] for v in views if v["kind"] == "ortho"}),
            "sections": sum(v["kind"] == "section" for v in views),
            "details": sum(v["kind"] == "detail" for v in views),
            "clearance": snap["meta"].get("annotation_clearance"),
            "empty_views": [v["name"] for v in views if v["kind"] != "pictorial" and not counts[v["name"]]],
            "dimensions_by_view": {v["name"]: counts[v["name"]] for v in views},
            "hidden_dimensions": sum(bool(d.get("ref_hidden")) for d in snap["dimensions"]),
            "unplaced": snap["meta"].get("unplaced", []), "scale": snap["title_block"].get("scale")}


def dimension_values(snap):
    return {d["name"]: (d.get("view"), d.get("value"), d.get("refs")) for d in snap["dimensions"]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
