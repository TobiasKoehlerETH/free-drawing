"""Offline drawing jobs for the desktop app, independent of Qt and MCP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Callable

from .backends import BackendCancelled, BackendError, HeadlessBackend
from .rules import inspect
from .workflow import LAYOUT_RULES
from .definition import verify_definition

DrawingCancelled = BackendCancelled
ProgressCallback = Callable[[dict], None]


@dataclass
class DrawingOptions:
    source: Path
    output_root: Path | None = None
    template: str = "A3"
    projection: str = "first"
    material: str = ""
    title: str = ""
    export_pdf: bool = True
    layout_autofix: bool = True
    verify_definition: bool = True
    timeout: float = 600


@dataclass
class DrawingResult:
    document: Path
    preview: Path | None
    pdf: Path | None
    report: Path
    report_json: Path
    inspection: dict
    integrity_errors: list[str]
    notices: list[str]
    seconds: float


def create_drawing(options: DrawingOptions, on_progress: ProgressCallback | None = None,
                   cancel_event: threading.Event | None = None) -> DrawingResult:
    """Create and reopen a drawing; return review findings without raising for them.

    Every job gets a new output directory. Cancellation raises DrawingCancelled
    and retains any artifacts already written. Integrity errors identify suspect
    saved dimensions and must prevent automatic opening as a finished drawing.
    """
    started = time.monotonic()
    _check_cancelled(cancel_event)
    source = Path(options.source).expanduser().resolve()
    _validate_options(options, source)
    backend = HeadlessBackend(timeout=options.timeout,
                              on_progress=partial(_engine_progress, on_progress),
                              cancel_event=cancel_event)
    if not backend.available():
        raise BackendError("FreeCAD was not found. Install FreeCAD or set FREECADCMD to FreeCADCmd.exe.")

    output = _new_output_directory(source, options.output_root)
    document = output / f"{source.stem}.FCStd"
    preview_path = output / f"{source.stem}.png"
    pdf_path = output / f"{source.stem}.pdf"
    report_path = output / f"{source.stem}.report.txt"
    report_json = output / f"{source.stem}.report.json"
    title_block = {"date_of_issue": datetime.now().astimezone().date().isoformat()}
    if options.material.strip():
        title_block["part_material"] = options.material.strip()
    args = {"source": str(source), "output": str(document), "template": options.template,
            "projection": options.projection, "scale": "auto", "views": "auto",
            "sections": True, "details": True, "dimensioned_only": True,
            "section_cut_only": True, "title_block": title_block}
    if options.title.strip():
        args["title"] = options.title.strip()
    _emit(on_progress, "create", "Creating the drawing")
    created, initial = backend.run([
        {"op": "create_drawing", "args": args},
        _snapshot_step(document),
    ])
    if not document.is_file():
        raise BackendError("FreeCAD did not save the drawing file.")
    notices = list(created.get("warnings") or [])

    _emit(on_progress, "verify", "Reopening and checking saved dimensions")
    snapshot = backend.run([_snapshot_step(document)])[0]
    errors = check_integrity(initial, snapshot)
    if options.layout_autofix and not errors:
        snapshot, fix_errors, fix_notices = _fix_layout(backend, document, snapshot, on_progress)
        errors.extend(fix_errors)
        notices.extend(fix_notices)
    _check_cancelled(cancel_event)
    definition_result=None
    if options.verify_definition and not errors:
        _emit(on_progress,'definition','Checking whether the drawing defines the part')
        try:
            definition_result=verify_definition(backend,document,source,output/'definition',max_repairs=2,cancel_event=cancel_event)
            snapshot=backend.run([_snapshot_step(document)])[0]
            notices.append('Drawing reconstruction: '+definition_result['status']+' (see definition/verification.json).')
        except BackendCancelled:
            raise
        except Exception as error:
            notices.append('Drawing reconstruction could not be verified: '+str(error))
    report = inspect(snapshot)
    inspection = report.to_dict(include_pass=True)

    _emit(on_progress, "export", "Saving the drawing preview and PDF" if options.export_pdf
          else "Saving the drawing preview")
    export_steps = [{"op": "render", "args": {"doc": str(document), "source": "schematic",
                                               "out_path": str(preview_path), "width": 1800}}]
    if options.export_pdf:
        export_steps.append({"op": "export", "args": {"doc": str(document), "source": "schematic",
                                                       "format": "pdf", "path": str(pdf_path)}})
    try:
        backend.run(export_steps)
    except BackendCancelled:
        raise
    except BackendError as error:
        notices.append(f"Preview/PDF export failed: {error}")
    preview = _verified_export(preview_path, b"\x89PNG\r\n\x1a\n", notices)
    pdf = _verified_export(pdf_path, b"%PDF-", notices) if options.export_pdf else None
    _check_cancelled(cancel_event)
    seconds = round(time.monotonic() - started, 1)
    errors = list(dict.fromkeys(errors))
    notices = list(dict.fromkeys(notices))
    report_data = dict(inspection, source=str(source), document=str(document),
                       created_at=datetime.now().astimezone().isoformat(), seconds=seconds,
                       integrity_errors=errors, notices=notices,
                       definition_verification=definition_result,
                       engine="offline FreeCADCmd", export_source="schematic")
    report_json.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    report_text = report.text()
    if errors:
        report_text += "\n\nSAVED DRAWING INTEGRITY ERRORS\n" + "\n".join(errors)
    if notices:
        report_text += "\n\nGENERATION NOTES\n" + "\n".join(notices)
    report_path.write_text(report_text + "\n", encoding="utf-8")
    _emit(on_progress, "complete", "Drawing saved; check the integrity report" if errors
          else "Drawing ready to open in FreeCAD")
    return DrawingResult(document, preview, pdf, report_path, report_json,
                         inspection, errors, notices, seconds)


def check_integrity(before: dict, reopened: dict) -> list[str]:
    """Check persisted dimension identities, references, and measured values."""
    errors = []
    old_dimensions = {dimension.get("name"): dimension for dimension in before.get("dimensions", [])}
    new_dimensions = {dimension.get("name"): dimension for dimension in reopened.get("dimensions", [])}
    if len(new_dimensions) != len(reopened.get("dimensions", [])):
        errors.append("Saved drawing contains duplicate dimension names.")
    for name in sorted(old_dimensions.keys() - new_dimensions.keys(), key=str):
        errors.append(f"{name}: dimension disappeared after reopening.")
    for name in sorted(new_dimensions.keys() - old_dimensions.keys(), key=str):
        errors.append(f"{name}: unexpected dimension appeared after reopening.")
    views = {view.get("name") for view in reopened.get("views", []) if view.get("kind") != "group"}
    for name, dimension in new_dimensions.items():
        value = dimension.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            errors.append(f"{name}: saved dimension has no valid measured value.")
        elif abs(value) < 1e-9:
            errors.append(f"{name}: saved dimension measures zero; check its references.")
        if not dimension.get("view") or dimension["view"] not in views:
            errors.append(f"{name}: dimension references a missing drawing view.")
        refs = dimension.get("refs") or []
        # TechDraw's projected edge and vertex indices can start at zero.
        if not refs or not all(isinstance(ref, str) and re.fullmatch(r"(?:Edge|Vertex)\d+", ref)
                               for ref in refs):
            errors.append(f"{name}: dimension has missing or invalid geometry references.")
        old = old_dimensions.get(name)
        if old is None:
            continue
        old_value = old.get("value")
        if isinstance(old_value, (int, float)) and isinstance(value, (int, float)) \
                and math.isfinite(old_value) and math.isfinite(value) \
                and not math.isclose(old_value, value, rel_tol=1e-7, abs_tol=1e-6):
            errors.append(f"{name}: measured value changed from {old_value:g} to {value:g} after reopening.")
        for field in ("type", "view", "refs", "signature"):
            if old.get(field) != dimension.get(field):
                errors.append(f"{name}: {field} changed after reopening.")
    return errors


def _fix_layout(backend, document, snapshot, on_progress):
    errors, notices, seen = [], [], set()
    for pass_number in range(1, 3):
        actions = [action for action in inspect(snapshot).auto_actions(LAYOUT_RULES)
                   if json.dumps(action, sort_keys=True) not in seen]
        if not actions:
            break
        seen.update(json.dumps(action, sort_keys=True) for action in actions)
        _emit(on_progress, "layout", f"Tidying dimension placement ({pass_number}/2)")
        applied, saved = backend.run([
            {"op": "apply_actions", "args": {"doc": str(document), "actions": actions}},
            _snapshot_step(document),
        ])
        for result in applied.get("results", []):
            if not result.get("ok"):
                notices.append(f"Layout adjustment {result.get('op', '')}: {result.get('error', 'failed')}")
        _emit(on_progress, "verify", "Checking the adjusted drawing after reopening")
        snapshot = backend.run([_snapshot_step(document)])[0]
        errors.extend(check_integrity(saved, snapshot))
        if errors:
            break
    return snapshot, errors, notices


def _validate_options(options, source):
    if source.suffix.lower() not in (".step", ".stp") or not source.is_file():
        raise ValueError("Choose an existing STEP file (.step or .stp).")
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        raise ValueError("The drawing timeout must be greater than zero.")
    if options.template not in ("A4", "A3", "A2", "A1", "A0"):
        raise ValueError("Choose an A4, A3, A2, A1 or A0 drawing sheet.")
    if options.projection not in ("first", "third"):
        raise ValueError("Projection must be 'first' or 'third'.")


def _new_output_directory(source, output_root):
    root = Path(output_root).expanduser().resolve() if output_root else source.parent / "Drawings"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{source.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    number = 1
    while True:
        output = root / (name if number == 1 else f"{name}-{number}")
        try:
            output.mkdir()
            return output
        except FileExistsError:
            number += 1


def _snapshot_step(document):
    return {"op": "snapshot", "args": {"doc": str(document), "include_geometry": True}}


def _verified_export(path, header, notices):
    try:
        with path.open("rb") as stream:
            valid = stream.read(len(header)) == header
    except OSError:
        valid = False
    if valid:
        return path
    notices.append(f"{path.suffix[1:].upper()} export was not created successfully.")
    return None


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DrawingCancelled("Drawing creation cancelled.")


def _emit(callback, stage, message, **extra):
    if callback:
        callback({**extra, "stage": stage, "message": message})


def _engine_progress(callback, event):
    stage = event.get("stage", "working")
    messages = {"import": "Reading the STEP geometry", "classify": "Analysing the part",
                "choose_views": "Choosing useful views", "projection": "Projecting the part",
                "plan_dimensions": "Planning dimensions", "layout": "Arranging the sheet",
                "place_dimensions": "Placing dimensions", "sections_details": "Creating sections and details",
                "op:snapshot": "Checking the drawing", "op:render": "Rendering the preview",
                "op:export": "Exporting the PDF", "op:apply_actions": "Adjusting the layout"}
    if stage in messages:
        _emit(callback, stage, messages[stage], **{key: value for key, value in event.items() if key != "stage"})
