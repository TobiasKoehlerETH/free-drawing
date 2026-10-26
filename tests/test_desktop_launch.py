"""Launcher contracts: installation selection, safe paths and page activation."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from free_drawing_mcp import desktop_launch as launch


def executable(directory: Path, name="FreeCAD.exe") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


@pytest.fixture
def clean_detection(monkeypatch, tmp_path):
    monkeypatch.delenv("FREECAD_GUI", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programs"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(launch, "detect_freecadcmd", lambda: None)
    monkeypatch.setattr(launch.shutil, "which", lambda _: None)


def test_explicit_gui_has_priority(monkeypatch, tmp_path, clean_detection):
    gui = executable(tmp_path / "custom installation")
    monkeypatch.setenv("FREECAD_GUI", str(gui))
    monkeypatch.setattr(launch, "detect_freecadcmd", Mock(side_effect=AssertionError("must prefer FREECAD_GUI")))
    assert launch.detect_freecad_gui() == gui.resolve()


def test_invalid_explicit_gui_is_not_silently_replaced(monkeypatch, tmp_path, clean_detection):
    monkeypatch.setenv("FREECAD_GUI", str(tmp_path / "missing.exe"))
    assert launch.detect_freecad_gui() is None


def test_gui_next_to_detected_command(monkeypatch, tmp_path, clean_detection):
    gui = executable(tmp_path / "FreeCAD 1.1" / "bin")
    command = executable(gui.parent, "FreeCADCmd.exe")
    monkeypatch.setattr(launch, "detect_freecadcmd", lambda: [str(command)])
    assert launch.detect_freecad_gui() == gui.resolve()


def test_known_installation(monkeypatch, tmp_path, clean_detection):
    gui = executable(tmp_path / "programs" / "FreeCAD 1.1" / "bin")
    assert launch.detect_freecad_gui() == gui.resolve()


def test_path_installation(monkeypatch, tmp_path, clean_detection):
    gui = executable(tmp_path / "portable")
    monkeypatch.setattr(launch.shutil, "which", lambda name: str(gui) if name == "FreeCAD" else None)
    assert launch.detect_freecad_gui() == gui.resolve()


def test_launch_uses_safe_arguments_and_python_literals(monkeypatch, tmp_path):
    directory = tmp_path / "drawing ' & $ test ü"
    directory.mkdir()
    document = directory / "part ' & $(inert).FCStd"
    document.write_bytes(b"original drawing")
    gui = executable(tmp_path / "FreeCAD installation")
    spawned = Mock(return_value=SimpleNamespace(pid=42))
    monkeypatch.setattr(launch.subprocess, "Popen", spawned)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_PLUGIN_PATH", "incompatible Qt plugins")
    process = launch.open_in_freecad(document, gui)
    argv = spawned.call_args.args[0]
    options = spawned.call_args.kwargs
    assert process.pid == 42
    assert len(argv) == 2 and argv[0] == str(gui.resolve())
    assert options["shell"] is False
    assert options["cwd"] == directory.resolve()
    assert "QT_QPA_PLATFORM" not in options["env"]
    assert "QT_PLUGIN_PATH" not in options["env"]
    macro = Path(argv[1])
    assert macro.parent == directory and macro.suffix == ".FCMacro"
    tree = ast.parse(macro.read_text(encoding="utf-8"))
    assignment = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "document_path" for target in node.targets
        )
    )
    assert ast.literal_eval(assignment.value) == str(document.resolve())
    assert document.read_bytes() == b"original drawing"


def test_macro_selects_drawing_page_after_opening(monkeypatch, tmp_path):
    """Exercise generated code, including the deferred callback and failure path."""
    document = tmp_path / "drawing.FCStd"
    status = tmp_path / "open.json"
    calls = []
    active = type("MDIViewPagePy", (), {"fitAll": lambda self: calls.append("fit")})()
    page = SimpleNamespace(
        Name="Page", TypeId="TechDraw::DrawPage",
        ViewObject=SimpleNamespace(show=lambda: calls.append("show")),
    )
    doc = SimpleNamespace(Name="Drawing", Objects=[page], recompute=lambda: calls.append("recompute"))
    app = SimpleNamespace(
        openDocument=lambda path: calls.append(("open", path)) or doc,
        setActiveDocument=lambda name: calls.append(("activate", name)),
    )
    gui = SimpleNamespace(
        activateWorkbench=lambda name: calls.append(("workbench", name)),
        getDocument=lambda name: SimpleNamespace(activeView=lambda: active),
        updateGui=lambda: calls.append("update"),
    )
    callbacks = []
    qt = SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay, callback: callbacks.append(callback)))
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt))
    exec(launch._opening_macro(document, status), {})
    assert calls[0] == ("open", str(document))
    assert "show" not in calls and not status.exists()
    callbacks.pop()()
    result = json.loads(status.read_text(encoding="utf-8"))
    assert result["opened"] is True and result["page"] == "Page"
    assert calls.index("recompute") < calls.index("show") < calls.index("fit")

    # A GUI view that is still the 3D model must never report success.
    active = SimpleNamespace()
    exec(launch._opening_macro(document, status), {})
    callbacks.pop()()
    result = json.loads(status.read_text(encoding="utf-8"))
    assert result["opened"] is False
    assert "did not activate the TechDraw" in result["error"]


@pytest.mark.parametrize("name", ["source.step", "source.txt"])
def test_rejects_non_drawing_before_launch(monkeypatch, tmp_path, name):
    path = tmp_path / name
    path.touch()
    spawned = Mock()
    monkeypatch.setattr(launch.subprocess, "Popen", spawned)
    with pytest.raises(ValueError, match="FCStd"):
        launch.open_in_freecad(path)
    spawned.assert_not_called()


def test_missing_gui_error_is_actionable(monkeypatch, tmp_path):
    document = tmp_path / "drawing.FCStd"
    document.touch()
    monkeypatch.setattr(launch, "detect_freecad_gui", lambda: None)
    with pytest.raises(FileNotFoundError, match="FREECAD_GUI"):
        launch.open_in_freecad(document)
    assert not list(tmp_path.glob("*.FCMacro"))
