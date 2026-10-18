"""Headless job lifecycle: timeouts report their stage and clean up."""

from pathlib import Path

import pytest

from free_drawing_mcp.backends import BackendError, HeadlessBackend, detect_freecadcmd

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT / "samples" / "parts" / "cross_hole_block.step"

pytestmark = [pytest.mark.freecad,
              pytest.mark.skipif(detect_freecadcmd() is None, reason="freecadcmd not installed")]


def test_timeout_reports_stage_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.delenv("FD_KEEP_FAILED_JOBS", raising=False)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    be = HeadlessBackend(timeout=12, job_root=str(jobs))
    with pytest.raises(BackendError) as err:
        be.run([{"op": "create_drawing", "args": {"source": str(PART), "output": str(tmp_path / "x.FCStd")}}])
    msg = str(err.value)
    assert "did not finish within 12s" in msg
    assert "create_drawing" in msg and "cross_hole_block" in msg
    assert "stuck in stage" in msg
    assert list(jobs.iterdir()) == []  # no leaked job directory


def test_timeout_can_keep_diagnostic_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_KEEP_FAILED_JOBS", "1")
    be = HeadlessBackend(timeout=12)
    with pytest.raises(BackendError) as err:
        be.run([{"op": "create_drawing", "args": {"source": str(PART), "output": str(tmp_path / "x.FCStd")}}])
    kept = str(err.value).split("job kept for diagnosis in ")[-1].strip()
    path = Path(kept)
    try:
        assert path.is_dir() and (path / "job.json").is_file()
    finally:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


def test_success_reports_stage_timings(tmp_path):
    be = HeadlessBackend(timeout=600)
    created = be.run([{"op": "create_drawing", "args": {"source": str(ROOT / "samples" / "parts" / "boss_plate.step"),
                                                         "output": str(tmp_path / "boss.FCStd")}}])[0]
    t = created["timings"]
    for stage in ("import", "choose_views", "projection", "plan_dimensions", "layout", "place_dimensions"):
        assert stage in t, t
    assert all(v >= 0 for v in t.values())
