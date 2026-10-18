"""Stage reporting for long drawing jobs.

``stage("projection", view="Top", direction=[0, 0, 1])`` appends one JSON line
to the file named by ``FD_PROGRESS`` (set by the headless runner) and keeps
per-stage timings in memory, so a timed-out job can say where it was and a
finished one can report where the time went. Without ``FD_PROGRESS`` only the
in-memory timings are kept.
"""

import json
import os
import time

_t0 = time.monotonic()
_last = [_t0, None]
timings = {}


def reset():
    global _t0
    _t0 = time.monotonic()
    _last[0], _last[1] = _t0, None
    timings.clear()


def stage(name, **info):
    now = time.monotonic()
    if _last[1] is not None:
        timings[_last[1]] = round(timings.get(_last[1], 0.0) + now - _last[0], 2)
    _last[0], _last[1] = now, name
    path = os.environ.get("FD_PROGRESS")
    if not path:
        return
    rec = {"stage": name, "t": round(now - _t0, 2), **info}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def finish():
    stage("done")
    return dict(timings)
