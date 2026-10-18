"""Headless entry point: ``freecadcmd -c "exec(open(runner).read())"``.

Reads a job file named by the ``FD_JOB`` environment variable::

    {"steps": [{"op": "snapshot", "args": {...}}, ...], "result": "out.json"}

and writes ``{"results": [...]}`` (or ``{"error", "traceback"}``) to
``result``. stdout is not used: freecadcmd swallows or mangles it on some
platforms.
"""

import json
import os
import sys
import traceback

_job_path = os.environ.get("FD_JOB")
_addon_dir = os.environ.get("FD_ADDON_DIR")
if _addon_dir and _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

with open(_job_path, encoding="utf-8") as _f:
    _job = json.load(_f)
if _job.get("progress"):
    os.environ["FD_PROGRESS"] = _job["progress"]

_out = {"results": []}
try:
    from fd_drawing import ops as _ops

    from fd_drawing import progress as _progress

    for _step in _job.get("steps", []):
        _progress.stage("op:" + _step["op"])
        _out["results"].append(_ops.run(_step["op"], _step.get("args") or {}))
except BaseException as _e:  # report everything, including SystemExit from user code
    _out["error"] = f"{type(_e).__name__}: {_e}"
    _out["traceback"] = traceback.format_exc()

with open(_job["result"], "w", encoding="utf-8") as _f:
    json.dump(_out, _f, ensure_ascii=False, default=str)
