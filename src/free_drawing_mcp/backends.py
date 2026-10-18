"""How the MCP server reaches FreeCAD.

* :class:`RpcBackend` talks XML-RPC to the FreeDrawingMCP addon running inside
  the FreeCAD GUI (default port 9876). Live documents, TechDraw's own export.
* :class:`HeadlessBackend` runs each request in a fresh ``freecadcmd``
  process against ``.FCStd`` files. No GUI needed; good for batch/CI.

Both run the same ``fd_drawing.ops`` operations, so results are identical.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import xmlrpc.client
from pathlib import Path
from typing import Any

ADDON_DIR = Path(__file__).resolve().parents[2] / "addon" / "FreeDrawingMCP"
DEFAULT_PORT = 9876


class BackendError(RuntimeError):
    pass


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float, headers=None):
        super().__init__(headers=headers or [])
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class RpcBackend:
    kind = "rpc"

    def __init__(self, host: str = "localhost", port: int = DEFAULT_PORT, token: str | None = None, timeout: float = 300):
        self.uri = f"http://{host}:{port}"
        self.headers = [("Authorization", f"Bearer {token}")] if token else []
        self.timeout = timeout

    def _proxy(self, timeout: float | None = None):
        return xmlrpc.client.ServerProxy(
            self.uri, allow_none=True,
            transport=_TimeoutTransport(timeout or self.timeout, self.headers),
        )

    def available(self) -> bool:
        try:
            with self._proxy(3) as p:
                return bool(p.ping())
        except Exception:
            return False

    def run(self, steps: list[dict[str, Any]]) -> list[Any]:
        try:
            with self._proxy() as p:
                res = p.drawing_ops(steps)
        except xmlrpc.client.Fault as e:
            raise BackendError(e.faultString) from e
        except OSError as e:
            raise BackendError(f"FreeCAD addon not reachable at {self.uri} ({e}); start it from the "
                               "'Free Drawing MCP' workbench or use --backend headless") from e
        if not isinstance(res, dict):
            raise BackendError(f"unexpected reply {res!r}")
        if res.get("error"):
            raise BackendError(res["error"] + ("\n" + res["traceback"] if res.get("traceback") else ""))
        return res["results"]

    def execute_code(self, code: str) -> dict[str, Any]:
        with self._proxy() as p:
            return p.execute_code(code)


def _read_progress(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def _last_stage(path: Path) -> str:
    stages = _read_progress(path)
    return stages[-1]["stage"] if stages else "startup"


def detect_freecadcmd() -> list[str] | None:
    env = os.environ.get("FREECADCMD")
    if env:
        return [env]
    for name in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe"):
        p = shutil.which(name)
        if p:
            return [p]
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for d in sorted(Path(root).glob("FreeCAD*"), reverse=True):
            exe = d / "bin" / "freecadcmd.exe"
            if exe.is_file():
                return [str(exe)]
    for p in ("/usr/bin/freecadcmd", "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"):
        if os.path.isfile(p):
            return [p]
    return None


class HeadlessBackend:
    kind = "headless"

    def __init__(self, command: list[str] | None = None, timeout: float = 600, job_root: str | None = None):
        self.command = command or detect_freecadcmd()
        self.timeout = timeout
        self.job_root = job_root  # where fd_job_* work folders go (default: system temp)

    def available(self) -> bool:
        return bool(self.command)

    def run(self, steps: list[dict[str, Any]]) -> list[Any]:
        if not self.command:
            raise BackendError("freecadcmd not found: install FreeCAD or pass --freecadcmd / set FREECADCMD")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise BackendError("timeout must be positive")
        work = Path(tempfile.mkdtemp(prefix="fd_job_", dir=self.job_root))
        keep = False
        started = time.monotonic()
        progress = work / "progress.jsonl"
        try:
            job = work / "job.json"
            result = work / "result.json"
            job.write_text(json.dumps({"steps": steps, "result": str(result), "progress": str(progress)},
                                      ensure_ascii=False), encoding="utf-8")
            runner = ADDON_DIR / "fd_drawing" / "runner.py"
            env = dict(os.environ, FD_JOB=str(job), FD_ADDON_DIR=str(ADDON_DIR), QT_QPA_PLATFORM="offscreen")
            argv = self.command + ["-c", f"exec(open({str(runner)!r}, encoding='utf-8').read())"]
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=self.timeout, env=env, cwd=os.getcwd())
            except subprocess.TimeoutExpired as e:
                # subprocess.run already killed and reaped the freecadcmd it started
                keep = bool(os.environ.get("FD_KEEP_FAILED_JOBS"))
                raise BackendError(self._timeout_message(steps, progress, time.monotonic() - started,
                                                         work if keep else None)) from e
            if not result.exists():
                keep = bool(os.environ.get("FD_KEEP_FAILED_JOBS"))
                tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-15:])
                raise BackendError(f"headless FreeCAD exited with {proc.returncode} without a result "
                                   f"(last stage: {_last_stage(progress)}):\n{tail}")
            out = json.loads(result.read_text(encoding="utf-8"))
        finally:
            if not keep:
                shutil.rmtree(work, ignore_errors=True)
        if out.get("error"):
            raise BackendError(out["error"] + "\n" + out.get("traceback", ""))
        return out["results"]

    def _timeout_message(self, steps, progress: Path, elapsed: float, kept: Path | None) -> str:
        ops = ", ".join(s.get("op", "?") for s in steps)
        src = next((s.get("args", {}).get("source") or s.get("args", {}).get("doc") for s in steps
                    if s.get("args", {}).get("source") or s.get("args", {}).get("doc")), None)
        stages = _read_progress(progress)
        last = stages[-1] if stages else {}
        detail = ", ".join(f"{k}={v}" for k, v in last.items() if k not in ("stage", "t"))
        trail = " -> ".join(f"{s['stage']}@{s.get('t', 0):g}s" for s in stages[-8:])
        msg = (f"headless FreeCAD did not finish within {self.timeout:g}s (ran {elapsed:.1f}s) "
               f"in '{ops}' for {src}: stuck in stage '{last.get('stage', 'startup')}'"
               + (f" ({detail})" if detail else "") + (f"; stages: {trail}" if trail else ""))
        if kept:
            msg += f"; job kept for diagnosis in {kept}"
        return msg

    def execute_code(self, code: str) -> dict[str, Any]:
        raise BackendError("execute_code needs the GUI addon (rpc backend)")


def make_backend(kind: str = "auto", host: str = "localhost", port: int = DEFAULT_PORT,
                 token: str | None = None, freecadcmd: list[str] | None = None):
    if kind == "rpc":
        return RpcBackend(host, port, token)
    if kind == "headless":
        return HeadlessBackend(freecadcmd)
    rpc = RpcBackend(host, port, token)
    if rpc.available():
        return rpc
    return HeadlessBackend(freecadcmd)
