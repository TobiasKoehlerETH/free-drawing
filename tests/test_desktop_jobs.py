"""Desktop jobs: output isolation, saved dimensions, cancellation and safe defaults."""

import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import threading

import pytest

from conftest import make_snapshot
from free_drawing_mcp import backends, desktop_jobs as jobs


class FakeBackend:
    def __init__(self, **kwargs):
        self.cancel_event = kwargs.get("cancel_event")
        self.calls = []
        self.snapshot = make_snapshot()
        self.reopen_change = None
        self.reopens = 0

    def available(self):
        return True

    def run(self, steps):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise jobs.DrawingCancelled("cancelled")
        self.calls.append(copy.deepcopy(steps))
        if steps[0]["op"] == "snapshot":
            self.reopens += 1
            if self.reopen_change:
                self.reopen_change(self.snapshot)
        results = []
        for step in steps:
            args = step.get("args", {})
            if step["op"] == "create_drawing":
                Path(args["output"]).write_bytes(b"saved drawing")
                self.snapshot["title_block"].pop("part_material", None)
                self.snapshot["title_block"].pop("general_tolerances", None)
                self.snapshot["annotations"] = []
                results.append({"warnings": []})
            elif step["op"] == "snapshot":
                results.append(copy.deepcopy(self.snapshot))
            elif step["op"] == "render":
                Path(args["out_path"]).write_bytes(b"\x89PNG\r\n\x1a\npreview")
                results.append({"path": args["out_path"]})
            elif step["op"] == "export":
                Path(args["path"]).write_bytes(b"%PDF-test")
                results.append({"path": args["path"]})
            elif step["op"] == "apply_actions":
                results.append({"results": [{"ok": True, "op": action["op"]}
                                             for action in args["actions"]]})
        return results


@pytest.fixture
def fake_engine(monkeypatch):
    engine = FakeBackend()

    def make_engine(**kwargs):
        engine.cancel_event = kwargs.get("cancel_event")
        return engine

    monkeypatch.setattr(jobs, "HeadlessBackend", make_engine)
    return engine


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "part.rev A ü.STEP"
    path.write_text("original source", encoding="utf-8")
    return path


def test_job_is_offline_uses_safe_defaults_and_reports_missing_specs(source, fake_engine):
    events = []
    result = jobs.create_drawing(jobs.DrawingOptions(source), events.append)
    args = fake_engine.calls[0][0]["args"]
    assert args["views"] == "auto"
    assert args["dimensioned_only"] is True and args["section_cut_only"] is True
    assert args["title_block"] == {"date_of_issue": datetime.now().astimezone().date().isoformat()}
    assert result.document.name == "part.rev A ü.FCStd"
    assert result.document.parent.parent == source.parent / "Drawings"
    assert source.read_text(encoding="utf-8") == "original source"
    assert result.preview.is_file() and result.pdf.is_file()
    assert result.report.is_file() and result.report_json.is_file()
    assert not result.integrity_errors
    assert result.inspection["summary"]["fail"] > 0  # review outcome, not a job error
    assert any(finding["rule"] == "K.1" for finding in result.inspection["findings"])
    assert fake_engine.reopens >= 1
    assert events[0]["stage"] == "create" and events[-1]["stage"] == "complete"
    assert all(event["stage"] and event["message"] for event in events)
    assert json.loads(result.report_json.read_text(encoding="utf-8"))["integrity_errors"] == []


def test_output_collisions_never_overwrite_prior_results(source, tmp_path, fake_engine, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 18, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(jobs, "datetime", FixedDatetime)
    options = jobs.DrawingOptions(source, output_root=tmp_path / "chosen", layout_autofix=False)
    first = jobs.create_drawing(options)
    first.document.write_bytes(b"keep prior result")
    second = jobs.create_drawing(options)
    assert first.document.parent != second.document.parent
    assert first.document.read_bytes() == b"keep prior result"
    assert second.document.parent.name.endswith("-2")
    assert first.document.parent.parent == options.output_root


def test_only_explicit_material_and_title_are_supplied(source, fake_engine):
    result = jobs.create_drawing(jobs.DrawingOptions(source, material=" EPDM ", title=" Cap ",
                                                     export_pdf=False, layout_autofix=False))
    args = fake_engine.calls[0][0]["args"]
    assert args["title_block"]["part_material"] == "EPDM" and args["title"] == "Cap"
    assert "general_tolerances" not in args["title_block"]
    assert result.pdf is None
    assert all(step["op"] != "export" for call in fake_engine.calls for step in call)


@pytest.mark.parametrize("change, phrase", [
    (lambda dim: dim.update(value=0), "changed from 80 to 0"),
    (lambda dim: dim.update(value=None), "no valid measured value"),
    (lambda dim: dim.update(value=float("nan")), "no valid measured value"),
    (lambda dim: dim.update(refs=[]), "missing or invalid geometry references"),
    (lambda dim: dim.update(refs=["Edge-1"]), "missing or invalid geometry references"),
    (lambda dim: dim.update(view="Gone"), "missing drawing view"),
    (lambda dim: dim.update(refs=["Vertex3", "Vertex4"]), "refs changed"),
    (lambda dim: dim.update(signature="other feature"), "signature changed"),
])
def test_integrity_detects_bad_values_and_changed_references(change, phrase):
    before = make_snapshot()
    reopened = copy.deepcopy(before)
    change(reopened["dimensions"][0])
    assert any(phrase in error for error in jobs.check_integrity(before, reopened))


def test_integrity_detects_lost_or_added_dimensions_and_accepts_stable_dimensions():
    before = make_snapshot()
    assert jobs.check_integrity(before, copy.deepcopy(before)) == []
    reopened = copy.deepcopy(before)
    reopened["dimensions"].pop()
    assert any("disappeared" in error for error in jobs.check_integrity(before, reopened))
    reopened["dimensions"][0]["name"] = "Unexpected"
    assert any("unexpected" in error for error in jobs.check_integrity(before, reopened))


def test_techdraw_zero_based_references_are_valid():
    snapshot = make_snapshot()
    snapshot["dimensions"][0]["refs"] = ["Vertex0", "Vertex2"]
    snapshot["dimensions"][3]["refs"] = ["Edge0"]
    assert jobs.check_integrity(snapshot, copy.deepcopy(snapshot)) == []


def test_persistence_failure_keeps_artifacts_but_skips_autofix(source, fake_engine):
    fake_engine.reopen_change = lambda snap: snap["dimensions"][0].update(value=0)
    result = jobs.create_drawing(jobs.DrawingOptions(source))
    assert result.document.is_file() and result.report.is_file()
    assert any("measures zero" in error for error in result.integrity_errors)
    assert all(step["op"] != "apply_actions" for call in fake_engine.calls for step in call)


def test_persistence_is_checked_again_after_layout_changes(source, fake_engine):
    fake_engine.snapshot["dimensions"][1]["text_box"] = list(fake_engine.snapshot["dimensions"][0]["text_box"])

    def corrupt_second_reopen(snapshot):
        if fake_engine.reopens == 2:
            snapshot["dimensions"][0]["value"] = 0

    fake_engine.reopen_change = corrupt_second_reopen
    result = jobs.create_drawing(jobs.DrawingOptions(source))
    assert any("changed from 80 to 0" in error for error in result.integrity_errors)
    assert fake_engine.reopens == 2
    assert sum(step["op"] == "apply_actions" for call in fake_engine.calls for step in call) == 1


def test_layout_fix_is_bounded_and_uses_only_safe_rule_actions(source, fake_engine, monkeypatch):
    real_inspect = jobs.inspect
    passes = []

    class ReportWithChangingLayout:
        def auto_actions(self, rules):
            assert rules == jobs.LAYOUT_RULES
            passes.append(1)
            return [{"op": "move_dimension", "name": "DimX", "x": 10 + len(passes), "y": 20}]

    def inspect_for_layout(snapshot):
        # The final report uses the real implementation after two passes.
        return ReportWithChangingLayout() if len(passes) < 2 else real_inspect(snapshot)

    monkeypatch.setattr(jobs, "inspect", inspect_for_layout)
    result = jobs.create_drawing(jobs.DrawingOptions(source))
    assert not result.integrity_errors
    assert sum(step["op"] == "apply_actions" for call in fake_engine.calls for step in call) == 2
    assert fake_engine.reopens == 3


def test_cancel_before_start_creates_no_output(source, fake_engine):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(jobs.DrawingCancelled):
        jobs.create_drawing(jobs.DrawingOptions(source), cancel_event=cancel)
    assert fake_engine.calls == []
    assert not (source.parent / "Drawings").exists()


def test_cancel_between_stages_stops_before_exports(source, fake_engine):
    cancel = threading.Event()

    def progress(event):
        if event["stage"] == "verify":
            cancel.set()

    with pytest.raises(jobs.DrawingCancelled):
        jobs.create_drawing(jobs.DrawingOptions(source), progress, cancel)
    assert len(fake_engine.calls) == 1


@pytest.mark.parametrize("filename", ["part.txt", "missing.step"])
def test_invalid_source_does_not_launch_engine(tmp_path, fake_engine, filename):
    path = tmp_path / filename
    if filename.endswith(".txt"):
        path.write_text("not a STEP")
    with pytest.raises(ValueError, match="STEP"):
        jobs.create_drawing(jobs.DrawingOptions(path))
    assert fake_engine.calls == []


def _fake_child(tmp_path, seconds=0.6):
    script = tmp_path / "fake_freecad.py"
    script.write_text(
        "import json, os, pathlib, time\n"
        "job = json.loads(pathlib.Path(os.environ['FD_JOB']).read_text())\n"
        "pathlib.Path(job['progress']).write_text(json.dumps({'stage':'projection','t':0}) + '\\n')\n"
        f"time.sleep({seconds})\n"
        "pathlib.Path(job['result']).write_text(json.dumps({'results':[{'ok':True}]}))\n",
        encoding="utf-8")
    return [sys.executable, str(script)]


def test_headless_progress_is_delivered_before_completion_and_work_folder_is_removed(tmp_path):
    command = _fake_child(tmp_path)
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    received = []

    def on_progress(event):
        assert not any(job_root.glob("*/result.json"))
        received.append(event)

    backend = backends.HeadlessBackend(command=command, timeout=5, job_root=str(job_root),
                                      on_progress=on_progress)
    assert backend.run([{"op": "status"}]) == [{"ok": True}]
    assert received == [{"stage": "projection", "t": 0}]
    assert list(job_root.iterdir()) == []


@pytest.mark.parametrize("failure", ["cancel", "timeout", "callback"])
def test_headless_failure_reaps_only_its_child_and_cleans_work_folder(tmp_path, monkeypatch, failure):
    command = _fake_child(tmp_path, seconds=20)
    job_root = tmp_path / "jobs"
    job_root.mkdir()
    monkeypatch.delenv("FD_KEEP_FAILED_JOBS", raising=False)
    cancel = threading.Event()
    children = []
    original_popen = backends.subprocess.Popen

    def record_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    def fail_callback(event):
        if failure == "callback":
            raise ValueError("callback failed")
        if failure == "cancel":
            cancel.set()

    monkeypatch.setattr(backends.subprocess, "Popen", record_child)
    backend = backends.HeadlessBackend(command=command, timeout=0.5 if failure == "timeout" else 5,
                                      job_root=str(job_root), cancel_event=cancel,
                                      on_progress=fail_callback)
    error_type = {"cancel": backends.BackendCancelled, "timeout": backends.BackendError,
                  "callback": ValueError}[failure]
    with pytest.raises(error_type):
        backend.run([{"op": "status"}])
    assert len(children) == 1 and children[0].poll() is not None
    assert list(job_root.iterdir()) == []


def test_partial_progress_line_preserves_finished_events(tmp_path):
    progress = tmp_path / "progress.jsonl"
    progress.write_text('{"stage":"import"}\n{"stage":', encoding="utf-8")
    assert backends._read_progress(progress) == [{"stage": "import"}]
