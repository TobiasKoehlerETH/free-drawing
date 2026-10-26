"""Drag/drop, background jobs and truthful desktop completion states."""

import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore, QtGui, QtTest

from free_drawing_mcp.desktop import DrawingWindow, dropped_step
from free_drawing_mcp.desktop_jobs import DrawingCancelled, DrawingResult


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def wait_for(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert condition(), "UI did not reach the expected state"


def result_at(tmp_path, errors=()):
    document = tmp_path / "drawing.FCStd"
    document.write_bytes(b"test drawing")
    report = tmp_path / "report.txt"
    report.write_text("Material and tolerances need review", encoding="utf-8")
    return DrawingResult(document, None, None, report, tmp_path / "report.json",
                         {"summary": {"fail": 2, "warn": 0, "review": 1}}, list(errors), [], 1.0)


def local_mime(path):
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
    return mime


def drop(app, window, path):
    mime = local_mime(path)
    location = window.drop_area.rect().center()
    enter = QtGui.QDragEnterEvent(location, QtCore.Qt.DropAction.CopyAction, mime,
                                 QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.drop_area, enter)
    event = QtGui.QDropEvent(QtCore.QPointF(location), QtCore.Qt.DropAction.CopyAction, mime,
                            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.drop_area, event)
    return enter.isAccepted(), event.isAccepted()


def test_file_drop_runs_in_background_and_opens_reviewable_drawing(app, tmp_path):
    source = tmp_path / "cap with spaces & unicode μ.STP"
    source.write_bytes(b"source")
    calls, opened = [], []
    main_thread = threading.get_ident()
    result = result_at(tmp_path)

    def runner(options, on_progress, cancel_event):
        calls.append((options.source, threading.get_ident()))
        on_progress({"message": "Checking dimensions"})
        return result

    def launcher(document):
        opened.append(document)
        macro = tmp_path / "open.FCMacro"
        macro.with_suffix(".json").write_text(json.dumps({"opened": True}))
        return SimpleNamespace(args=["FreeCAD", str(macro)])

    window = DrawingWindow(runner, launcher)
    window.show()
    try:
        assert drop(app, window, source) == (True, True)
        wait_for(app, lambda: window.worker is None and window.launch_status is None)
        assert calls == [(source.resolve(), calls[0][1])]
        assert calls[0][1] != main_thread
        assert opened == [result.document]
        assert window.drop_area.heading.text() == "Opened in FreeCAD"
        assert "review" in window.status.text().lower()
        assert source.read_bytes() == b"source"
    finally:
        window.close()


def test_drop_rejects_remote_multiple_and_directory_inputs(app, tmp_path):
    directory = tmp_path / "folder.step"
    directory.mkdir()
    assert dropped_step(local_mime(directory)) is None
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl("https://example.com/model.step")])
    assert dropped_step(mime) is None
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(source))] * 2)
    assert dropped_step(mime) is None


def test_select_file_starts_the_same_job(app, tmp_path, monkeypatch):
    source = tmp_path / "selected.step"
    source.write_bytes(b"source")
    requested = []
    result = result_at(tmp_path)

    def runner(options, **kwargs):
        requested.append(options.source)
        return result

    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    macro = tmp_path / "open.FCMacro"
    macro.with_suffix(".json").write_text(json.dumps({"opened": True}))
    window = DrawingWindow(runner, lambda path: SimpleNamespace(args=["FreeCAD", str(macro)]))
    window.show()
    try:
        QtTest.QTest.mouseClick(window.drop_area.choose, QtCore.Qt.MouseButton.LeftButton)
        wait_for(app, lambda: window.worker is None and window.launch_status is None)
        assert requested == [source.resolve()]
        assert window.drop_area.heading.text() == "Opened in FreeCAD"
    finally:
        window.close()


def test_integrity_errors_prevent_automatic_opening(app, tmp_path):
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    result = result_at(tmp_path, ["Dimension changed from 4 to 0 after reopening"])
    opened = []
    window = DrawingWindow(lambda *a, **k: result, lambda path: opened.append(path))
    window.show()
    try:
        window.start_file(source)
        wait_for(app, lambda: window.worker is None)
        assert not opened
        assert window.drop_area.heading.text() == "Drawing needs checking"
        assert window.notes_button.isVisible()
        assert window.open_button.isVisible()
    finally:
        window.close()


def test_cancel_remains_responsive_and_does_not_open_freecad(app, tmp_path):
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    entered = threading.Event()
    opened = []

    def runner(options, on_progress, cancel_event):
        entered.set()
        cancel_event.wait(3)
        raise DrawingCancelled("cancelled")

    window = DrawingWindow(runner, lambda path: opened.append(path))
    window.show()
    try:
        window.start_file(source)
        wait_for(app, entered.is_set)
        assert drop(app, window, source) == (False, False)
        QtTest.QTest.mouseClick(window.cancel_button, QtCore.Qt.MouseButton.LeftButton)
        wait_for(app, lambda: window.worker is None)
        assert window.drop_area.heading.text() == "Cancelled"
        assert not opened
        assert window.drop_area.choose.isEnabled()
    finally:
        window.cancel_job()
        window.close()


def test_opening_is_not_reported_as_success_before_confirmation(app, tmp_path):
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    macro = tmp_path / "open.FCMacro"
    result = result_at(tmp_path)
    window = DrawingWindow(lambda *a, **k: result,
                           lambda path: SimpleNamespace(args=["FreeCAD", str(macro)]))
    window.show()
    try:
        window.start_file(source)
        wait_for(app, lambda: window.worker is None)
        assert window.launch_status is not None
        assert window.drop_area.heading.text() != "Opened in FreeCAD"
        macro.with_suffix(".json").write_text(json.dumps({"opened": False, "error": "No drawing page"}))
        window.tick()
        assert window.launch_status is None
        assert "attention" in window.status.text()
        assert window.open_button.isEnabled()
    finally:
        window.close()


def test_closing_active_job_waits_for_cancellation(app, tmp_path):
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    started = threading.Event()

    def runner(options, on_progress, cancel_event):
        started.set()
        cancel_event.wait(3)
        raise DrawingCancelled("cancelled")

    window = DrawingWindow(runner, lambda path: None)
    window.show()
    window.start_file(source)
    wait_for(app, started.is_set)
    window.close()
    wait_for(app, lambda: window.worker is None and not window.isVisible())
