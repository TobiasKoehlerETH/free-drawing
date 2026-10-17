"""free-drawing-check on saved snapshot JSON files (no FreeCAD needed)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

from free_drawing_mcp.cli import main

FAILING = FIXTURES / "plate_hole_pattern.snapshot.json"  # K.1 material missing -> FAIL


def test_cli_inspects_snapshot_and_returns_fail_code(capsys):
    code = main([str(FAILING)])
    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("DRAWING CHECK  samples/out/plate_hole_pattern.FCStd > Page")
    assert "RESULT FAIL" in out
    assert "FAIL   K.1   material not specified" in out
    assert "FIXES" in out


def test_cli_passing_snapshot_returns_zero(tmp_path, capsys, snap):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    assert main([str(p)]) == 0
    assert "RESULT PASS" in capsys.readouterr().out


def test_cli_json_output(capsys):
    code = main([str(FAILING), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["summary"]["result"] == "FAIL"
    assert [f["rule"] for f in data["findings"] if f["status"] == "fail"] == ["K.1"]


def test_cli_rules_filter(capsys):
    code = main([str(FAILING), "--rules", "E.1", "E.10"])
    out = capsys.readouterr().out
    assert code == 0 and "RESULT PASS" in out and "K.1" not in out


def test_cli_survives_non_utf8_stdout():
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=str(ROOT / "src"))
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run([sys.executable, "-m", "free_drawing_mcp.cli", str(FIXTURES / "l_bracket.snapshot.json")],
                          capture_output=True, env=env, timeout=60)
    err = proc.stderr.decode(errors="replace")
    assert "Traceback" not in err, err[-500:]
    out = proc.stdout.decode("cp1252", errors="replace")
    assert proc.returncode == 1 and "RESULT FAIL" in out and "FIXES" in out
