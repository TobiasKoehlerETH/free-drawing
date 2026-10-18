"""Batch-test the drawing pipeline on every sample part.

    python samples/run_samples.py                 # all parts in samples/parts
    python samples/run_samples.py --generate      # regenerate parts first
    python samples/run_samples.py flange tube     # only matching names
    python samples/run_samples.py --extra C:/path/to/more/steps

For each part: create the drawing headless, inspect it against the guideline
rules, render a PNG with findings highlighted, and write
``samples/out/summary.md``. Each part runs in its own freecadcmd process, so
one bad file cannot take the batch down.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from free_drawing_mcp.backends import HeadlessBackend, detect_freecadcmd  # noqa: E402

PARTS = ROOT / "samples" / "parts"
OUT = ROOT / "samples" / "out"
VIEWS = "auto"


def generate():
    cmd = detect_freecadcmd()
    if not cmd:
        sys.exit("freecadcmd not found")
    script = ROOT / "samples" / "generate_parts.py"
    subprocess.run(cmd + ["-c", f"exec(open({str(script)!r}, encoding='utf-8').read())"], cwd=ROOT,
                   capture_output=True, timeout=600)
    print((PARTS / "generate_report.txt").read_text(encoding="utf-8"))


def run_one(step: Path) -> dict:
    from free_drawing_mcp.rules import inspect, highlights

    t0 = time.time()
    fcstd = OUT / f"{step.stem}.FCStd"
    png = OUT / f"{step.stem}.png"
    backend = HeadlessBackend(timeout=900)
    res = {"part": step.stem}
    try:
        created = backend.run([
            {"op": "create_drawing", "args": {"source": str(step), "output": str(fcstd), "views": VIEWS}},
        ])[0]
        if AUTOFIX:  # same safe layout pass create_drawing applies through MCP
            from free_drawing_mcp import workflow

            _, log, _ = workflow.autofix(backend, str(fcstd), rules=workflow.LAYOUT_RULES)
            res["autofix"] = log
        snap = backend.run([{"op": "snapshot", "args": {"doc": str(fcstd)}}])[0]
        report = inspect(snap)
        (OUT / f"{step.stem}.snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        backend.run([{"op": "render", "args": {"doc": str(fcstd), "out_path": str(png), "width": 2400,
                                               "highlights": highlights(report)}}])
        (OUT / f"{step.stem}.report.txt").write_text(report.text(), encoding="utf-8")
        res.update(ok=True, created=created, summary=report.summary(), seconds=round(time.time() - t0, 1))
    except Exception as e:
        res.update(ok=False, error=str(e).splitlines()[0][:300], seconds=round(time.time() - t0, 1))
    return res


AUTOFIX = True


def main():
    global OUT, VIEWS, AUTOFIX
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--extra", action="append", default=[], help="extra folder or STEP file")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", type=Path, default=OUT, help="artifact directory")
    ap.add_argument("--views", nargs="+", choices=["Front", "Top", "Right", "Left", "Bottom"],
                    help="fixed principal views for every input (default: automatic selection)")
    ap.add_argument("--no-autofix", action="store_true", help="skip the safe layout autofix pass")
    args = ap.parse_args()
    OUT = args.out.resolve()
    AUTOFIX = not args.no_autofix
    if args.views and "Front" not in args.views:
        ap.error("--views must include Front")
    VIEWS = list(dict.fromkeys(args.views)) if args.views else "auto"
    if args.generate or not any(PARTS.glob("*.step")):
        generate()
    OUT.mkdir(parents=True, exist_ok=True)
    steps = sorted(PARTS.glob("*.step"))
    for extra in args.extra:
        p = Path(extra)
        steps += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in (".step", ".stp")) if p.is_dir() else [p]
    steps = list(dict.fromkeys(s.resolve() for s in steps))
    if args.names:
        steps = [s for s in steps if any(n.lower() in s.stem.lower() for n in args.names)]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run_one, steps))

    lines = ["# Sample drawing run", "", "Generation success does not establish drawing release readiness.", "",
             "| part | result | principal views | sections/details | scale | dims | fail | warn | review | score | s |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r["ok"]:
            lines.append(f"| {r['part']} | ERROR: {r['error']} | | | | | | | | | {r['seconds']} |")
            continue
        c, sm = r["created"], r["summary"]
        lines.append(f"| {r['part']} | {sm['result']} | {','.join(c['views'])} | {len(c.get('sections', []))}/{len(c.get('details', []))} | {c['scale']} | {c['dimensions']} | "
                     f"{sm['fail']} | {sm['warn']} | {sm['review']} | {sm['score']} | {r['seconds']} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
