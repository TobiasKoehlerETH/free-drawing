"""Minimal desktop entry point: drop a STEP file and open its drawing in FreeCAD."""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .desktop_jobs import DrawingCancelled, DrawingOptions, DrawingResult, create_drawing
from .desktop_launch import detect_freecad_gui, open_in_freecad

LOG = logging.getLogger("free_drawing.desktop")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drop a STEP file to create and open a FreeCAD drawing.")
    parser.add_argument("source", nargs="?", type=Path, help="optional STEP file to start with")
    args = parser.parse_args(argv)
    configure_logging()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    app.setApplicationName("Free Drawing")
    app.setOrganizationName("Free Drawing")
    app.setStyle("Fusion")
    body_font, _ = ensure_fonts()
    app.setFont(QtGui.QFont(body_font, 10))
    app.styleHints().setColorScheme(QtCore.Qt.ColorScheme.Light)
    window = DrawingWindow()
    window.show()
    if args.source:
        QtCore.QTimer.singleShot(100, lambda: window.start_file(args.source))
    return app.exec()


class DrawingWindow(QtWidgets.QWidget):
    """One-file workflow; all CAD work runs outside the UI thread."""

    def __init__(self, job_runner=create_drawing, launcher=open_in_freecad, parent=None):
        super().__init__(parent)
        body_font, emphasis_font = ensure_fonts()
        self.job_runner, self.launcher = job_runner, launcher
        self.worker = None
        self.result = None
        self.error_details = ""
        self.started = 0.0
        self.close_when_idle = False
        self.launch_process = None
        self.launch_status = None
        self.launch_started = 0.0
        self.setWindowTitle("Free Drawing")
        self.setMinimumSize(500, 420)
        self.resize(560, 450)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE.replace("__BODY_FONT__", body_font).replace("__EMPHASIS_FONT__", emphasis_font))
        self.setWindowIcon(document_icon())

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 25, 28, 20)
        layout.setSpacing(14)
        title = QtWidgets.QLabel("FREE DRAWING")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QtWidgets.QLabel("Drop a STEP file. Open the drawing in FreeCAD.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        self.drop_area = DropArea()
        self.drop_area.file_selected.connect(self.start_file)
        self.drop_area.choose_requested.connect(self.choose_file)
        self.drop_area.rejected.connect(self.show_input_error)
        layout.addWidget(self.drop_area, 1)

        status_row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("Ready")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.elapsed = QtWidgets.QLabel("")
        self.elapsed.setObjectName("muted")
        status_row.addWidget(self.elapsed)
        layout.addLayout(status_row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        actions = QtWidgets.QHBoxLayout()
        self.notes_button = text_button("Review notes", self.show_notes)
        self.folder_button = text_button("Show files", self.show_files)
        self.open_button = text_button("Open in FreeCAD", self.open_result)
        self.cancel_button = text_button("Cancel", self.cancel_job)
        for button in (self.notes_button, self.folder_button, self.open_button):
            button.hide()
            actions.addWidget(button)
        actions.addStretch()
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        if detect_freecad_gui() is None:
            self.show_input_error("Install FreeCAD to create and open drawings.")

    @QtCore.Slot()
    def choose_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose STEP file", "", "STEP files (*.step *.stp *.STEP *.STP)")
        if path:
            self.start_file(Path(path))

    @QtCore.Slot(object)
    def start_file(self, source):
        if self.worker is not None or self.launch_status is not None:
            return
        source = Path(source).expanduser()
        if source.suffix.lower() not in (".step", ".stp") or not source.is_file():
            self.show_input_error("Choose an existing .step or .stp file.")
            return
        self.result = None
        self.error_details = ""
        self.notes_button.setText("Review notes")
        self.started = time.monotonic()
        self.drop_area.set_busy(True, source.name)
        self.status.setText("Starting…")
        self.progress.setRange(0, 0)
        for button in (self.notes_button, self.folder_button, self.open_button):
            button.hide()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.worker = DrawingWorker(source.resolve(), self.job_runner, self)
        self.worker.progress_event.connect(self.job_progress)
        self.worker.completed.connect(self.job_ready)
        self.worker.failed.connect(self.job_failed)
        self.worker.finished.connect(self.thread_finished)
        self.worker.start()

    @QtCore.Slot(dict)
    def job_progress(self, event):
        if self.worker is not None and not self.worker.cancel_event.is_set():
            self.status.setText(event.get("message", "Creating drawing…"))

    @QtCore.Slot(object)
    def job_ready(self, result: DrawingResult):
        self.result = result
        self.folder_button.show()
        self.notes_button.show()
        self.open_button.show()
        self.cancel_button.hide()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if result.integrity_errors:
            self.error_details = "\n".join(result.integrity_errors)
            self.drop_area.set_result("Drawing needs checking", "Some saved dimensions could not be verified.", "warning")
            self.status.setText("Automatic opening stopped. See review notes.")
        else:
            self.drop_area.set_result("Drawing created", result.document.name, "success")
            if not self.close_when_idle:
                self.open_result()

    @QtCore.Slot(str, bool)
    def job_failed(self, message, cancelled):
        self.cancel_button.hide()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.error_details = message
        if cancelled:
            self.drop_area.set_result("Cancelled", "Drop a file when you’re ready to try again.", "idle")
            self.status.setText("Job stopped")
        else:
            self.drop_area.set_result("Couldn’t create this drawing", "Your original file is unchanged.", "warning")
            self.status.setText(message.splitlines()[0][:160])
            self.notes_button.setText("Details")
            self.notes_button.show()

    @QtCore.Slot()
    def thread_finished(self):
        finished = self.sender()
        if finished is self.worker:
            self.worker = None
        if finished:
            finished.deleteLater()
        if self.launch_status is None:
            self.drop_area.set_busy(False)
        if self.close_when_idle:
            self.close()

    @QtCore.Slot()
    def cancel_job(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Stopping…")

    @QtCore.Slot()
    def open_result(self):
        if self.result is None or self.launch_status is not None:
            return
        try:
            self.launch_process = self.launcher(self.result.document)
            self.launch_status = Path(self.launch_process.args[1]).with_suffix(".json")
            self.launch_started = time.monotonic()
            self.status.setText("Opening the drawing in FreeCAD…")
            self.open_button.setEnabled(False)
            self.drop_area.set_busy(True)
            self.progress.setRange(0, 0)
        except Exception as error:
            LOG.exception("Could not launch FreeCAD")
            self.error_details = str(error)
            self.status.setText("Drawing saved. FreeCAD could not be opened.")
            self.notes_button.show()
            self.drop_area.set_busy(False)

    @QtCore.Slot()
    def tick(self):
        if self.worker is not None and self.started:
            seconds = int(time.monotonic() - self.started)
            self.elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")
        if self.launch_status is None:
            return
        try:
            data = json.loads(self.launch_status.read_text(encoding="utf-8")) if self.launch_status.exists() else None
        except (OSError, ValueError):
            data = None
        if data is not None:
            self.finish_opening(data)
        elif time.monotonic() - self.launch_started > 240:
            self.finish_opening({"ok": False, "error": "FreeCAD did not confirm opening the drawing. It may still be starting."})

    def finish_opening(self, data):
        self.launch_status = None
        self.open_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.drop_area.set_busy(False)
        if data.get("opened"):
            self.drop_area.set_result("Opened in FreeCAD", self.result.document.name, "success")
            summary = self.result.inspection.get("summary", {})
            count = sum(summary.get(key, 0) for key in ("fail", "warn", "review"))
            self.status.setText("Drawing ready · review notes available" if count else "Drawing ready")
        else:
            self.error_details = str(data.get("error", "The drawing could not be displayed in FreeCAD."))
            self.status.setText("Drawing saved · opening needs attention")
            self.drop_area.set_result("Drawing saved", "Use Open in FreeCAD to try again.", "warning")

    @QtCore.Slot(str)
    def show_input_error(self, message):
        self.status.setText(message)

    @QtCore.Slot()
    def show_notes(self):
        content = self.error_details
        if self.result:
            try:
                content += "\n\n" + self.result.report.read_text(encoding="utf-8")
            except OSError:
                content += "\n\nReview report is unavailable."
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Drawing review")
        dialog.resize(650, 430)
        box = QtWidgets.QVBoxLayout(dialog)
        text = QtWidgets.QPlainTextEdit(content.strip() or "No review notes.")
        text.setReadOnly(True)
        box.addWidget(text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        dialog.exec()

    @QtCore.Slot()
    def show_files(self):
        if self.result:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.result.document.parent)))

    def dragEnterEvent(self, event):
        self.drop_area.dragEnterEvent(event)

    def dropEvent(self, event):
        self.drop_area.dropEvent(event)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.close_when_idle = True
            self.cancel_job()
            event.ignore()
        else:
            event.accept()


class DrawingWorker(QtCore.QThread):
    progress_event = QtCore.Signal(dict)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str, bool)

    def __init__(self, source, runner, parent=None):
        super().__init__(parent)
        self.source, self.runner = source, runner
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.runner(DrawingOptions(source=self.source),
                                 on_progress=self.progress_event.emit, cancel_event=self.cancel_event)
            self.completed.emit(result)
        except DrawingCancelled:
            self.failed.emit("Cancelled", True)
        except Exception as error:
            LOG.exception("Drawing job failed")
            self.failed.emit(str(error), self.cancel_event.is_set())


class DropArea(QtWidgets.QFrame):
    file_selected = QtCore.Signal(object)
    choose_requested = QtCore.Signal()
    rejected = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.busy = False
        self.setObjectName("dropArea")
        self.setProperty("tone", "idle")
        self.setAcceptDrops(True)
        self.setAccessibleName("Drop a STEP file here")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 22)
        layout.setSpacing(9)
        layout.addStretch()
        icon = QtWidgets.QLabel()
        icon.setPixmap(document_icon().pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        self.heading = QtWidgets.QLabel("DROP A STEP FILE")
        self.heading.setObjectName("dropTitle")
        self.heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.heading)
        self.description = QtWidgets.QLabel(".STEP  /  .STP")
        self.description.setObjectName("muted")
        self.description.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.choose = QtWidgets.QPushButton("SELECT FILE")
        self.choose.setObjectName("choose")
        self.choose.setAccessibleName("Choose STEP file")
        self.choose.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.choose.setFixedWidth(140)
        self.choose.clicked.connect(self.choose_requested)
        layout.addWidget(self.choose, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()

    def set_busy(self, busy, filename=None):
        self.busy = busy
        self.choose.setEnabled(not busy)
        if filename:
            self.heading.setText("Creating your drawing")
            self.description.setText(filename)
            self.description.setToolTip(filename)
            self.set_tone("idle")

    def set_result(self, heading, description, tone):
        self.heading.setText(heading)
        self.description.setText(description)
        self.choose.setText("Choose another file")
        self.choose.setFixedWidth(170)
        self.set_tone(tone)

    def set_tone(self, tone):
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):
        if not self.busy and dropped_step(event.mimeData()) is not None:
            self.set_tone("hover")
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_tone("idle")
        event.accept()

    def dropEvent(self, event):
        self.set_tone("idle")
        if self.busy:
            event.ignore()
            return
        source = dropped_step(event.mimeData())
        if source is None:
            self.rejected.emit("Drop one .step or .stp file at a time.")
            event.ignore()
            return
        event.acceptProposedAction()
        self.file_selected.emit(source)


def dropped_step(mime):
    urls = mime.urls() if mime.hasUrls() else []
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    source = Path(urls[0].toLocalFile())
    return source if source.suffix.lower() in (".step", ".stp") and source.is_file() else None


def text_button(label, callback):
    button = QtWidgets.QPushButton(label)
    button.setObjectName("textButton")
    button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(callback)
    return button


def document_icon():
    pixmap = QtGui.QPixmap(64, 64)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor("#333333"), 1.5))
    painter.setBrush(QtGui.QColor("#ffffff"))
    path = QtGui.QPainterPath()
    path.moveTo(17, 8)
    path.lineTo(38, 8)
    path.lineTo(49, 19)
    path.lineTo(49, 55)
    path.lineTo(17, 55)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(38, 8, 38, 20)
    painter.drawLine(38, 20, 49, 20)
    painter.drawLine(24, 31, 41, 31)
    painter.drawLine(24, 39, 37, 39)
    painter.drawLine(24, 47, 41, 47)
    painter.end()
    return QtGui.QIcon(pixmap)


def configure_logging():
    directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Free Drawing"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "desktop.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)


def ensure_fonts():
    """Use the A+P Futura typeface from local fonts without redistributing it."""
    if not getattr(ensure_fonts, "loaded", False):
        if os.name == "nt":
            system_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            font_folders = [system_fonts]
            local_data = os.environ.get("LOCALAPPDATA")
            if local_data:
                font_folders.append(Path(local_data) / "Microsoft" / "Windows" / "Fonts")
            for folder in font_folders:
                if folder.is_dir():
                    for path in folder.iterdir():
                        if "futura" in path.name.lower() and path.suffix.lower() in (".ttf", ".otf"):
                            QtGui.QFontDatabase.addApplicationFont(str(path))
            # The offscreen Windows platform does not discover system fonts.
            for name in ("arial.ttf", "arialbd.ttf"):
                path = system_fonts / name
                if path.is_file():
                    QtGui.QFontDatabase.addApplicationFont(str(path))
        ensure_fonts.loaded = True
    families = set(QtGui.QFontDatabase.families())
    body = next((name for name in ("Futura LT Book", "Futura LT", "Futura PT", "Futura Lt BT", "Futura", "Arial")
                 if name in families), "sans-serif")
    emphasis = next((name for name in ("Futura LT Medium", "Futura LT", "Futura PT", "Futura Md BT", "Futura")
                     if name in families), body)
    return body, emphasis


STYLE = """
QWidget { background: #f5f5f3; color: #1c1c1c; font-family: '__BODY_FONT__'; }
QLabel { background: transparent; }
QLabel#title { font-family: '__BODY_FONT__'; font-size: 24px; font-weight: 300; letter-spacing: 3px; color: #151515; }
QLabel#subtitle { color: #606060; font-size: 14px; }
QLabel#muted { color: #666666; font-size: 12px; letter-spacing: 1px; }
QLabel#status { color: #515151; font-size: 13px; }
QLabel#dropTitle { font-family: '__EMPHASIS_FONT__'; font-size: 16px; font-weight: 500; letter-spacing: 2px; color: #242424; }
QFrame#dropArea { background: #ffffff; border: 1px dashed #c6c6c6; border-radius: 2px; }
QFrame#dropArea[tone='hover'] { background: #fafafa; border-color: #333333; }
QFrame#dropArea[tone='success'] { border: 1px solid #777777; }
QFrame#dropArea[tone='warning'] { border: 1px dashed #777777; }
QPushButton#choose { font-family: '__EMPHASIS_FONT__'; background: #202020; color: white; border: 1px solid #202020; border-radius: 2px; padding: 10px 16px; font-size: 12px; font-weight: 500; letter-spacing: 1px; }
QPushButton#choose:hover { background: #000000; border-color: #000000; }
QPushButton#choose:disabled { background: #e5e5e5; border-color: #dddddd; color: #777777; }
QPushButton#textButton { background: transparent; border: none; color: #505050; padding: 4px 2px; font-size: 13px; }
QPushButton#textButton:hover { color: black; }
QPushButton#textButton:disabled { color: #888888; }
QProgressBar { border: none; background: #e0e0e0; border-radius: 0px; }
QProgressBar::chunk { background: #252525; border-radius: 0px; }
QPlainTextEdit { background: white; color: #242424; border: 1px solid #cccccc; padding: 8px; }
"""


if __name__ == "__main__":
    raise SystemExit(main())
