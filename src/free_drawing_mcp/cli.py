"""Command line access to the same pipeline the MCP tools use.

    free-drawing-check make part.step [-o part_drawing.FCStd] [--png out.png]
    free-drawing-check inspect part_drawing.FCStd [--json] [--rules E.10 A.8]
    free-drawing-check autofix part_drawing.FCStd
    free-drawing-check snapshot.json            # inspect a saved snapshot, no FreeCAD needed
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from . import workflow
from .backends import make_backend
from .rules import highlights, inspect


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # reports contain ⌀, ×, ·
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="free-drawing-check")
    ap.add_argument("command", help="make | inspect | autofix | render | <snapshot.json>")
    ap.add_argument("target", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--png")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rules", nargs="*")
    ap.add_argument("--backend", default="auto", choices=["auto", "rpc", "headless"])
    ap.add_argument("--purpose", default=None, choices=["manufacturing", "assembly", "reference", "change_notice"])
    ap.add_argument("--no-layout-autofix", action="store_true", help="make: skip the safe layout fixes")
    a = ap.parse_args(argv)

    if a.command.lower().endswith(".json") and a.target is None:
        snap = json.loads(Path(a.command).read_text(encoding="utf-8"))
        rep = inspect(snap, rules=a.rules, purpose=a.purpose)
        print(json.dumps(rep.to_dict(), indent=1, ensure_ascii=False) if a.json else rep.text())
        return 1 if rep.result() == "FAIL" else 0

    be = make_backend(a.backend)
    target = str(Path(a.target).resolve()) if a.target else None
    if a.command == "make":
        out = a.output or str(Path(target).with_name(Path(target).stem + "_drawing.FCStd"))
        args = {"source": target, "output": out}
        if a.purpose:
            args["purpose"] = a.purpose
        created = be.run([{"op": "create_drawing", "args": args}])[0]
        print(json.dumps(created, indent=1, ensure_ascii=False))
        target = created["file"]
        if not a.no_layout_autofix:
            for line in workflow.autofix(be, target, rules=workflow.LAYOUT_RULES, purpose=a.purpose)[1]:
                print("layout autofix", line)
    if a.command in ("make", "inspect", "autofix", "render"):
        if a.command == "autofix":
            for line in workflow.autofix(be, target, rules=a.rules, purpose=a.purpose)[1]:
                print(line)
        snap = be.run([{"op": "snapshot", "args": {"doc": target}}])[0]
        rep = inspect(snap, rules=a.rules, purpose=a.purpose)
        print(json.dumps(rep.to_dict(), indent=1, ensure_ascii=False) if a.json else rep.text())
        if a.png:
            img = be.run([{"op": "render", "args": {"doc": target, "highlights": highlights(rep), "width": 2400}}])[0]
            Path(a.png).write_bytes(base64.b64decode(img["png_base64"]))
            print(f"preview: {a.png}")
        return 1 if rep.result() == "FAIL" else 0
    ap.error(f"unknown command {a.command}")


if __name__ == "__main__":
    sys.exit(main())
