"""XML-RPC server inside FreeCAD for the free-drawing MCP server.

Methods:

``ping()``                      -> True
``get_rpc_status()``            -> versions, port, GUI state (no GUI thread needed)
``drawing_ops(steps)``          -> {"results": [...]} | {"error", "traceback"}
``execute_code(code)``          -> {"success", "output"|"error"}

Security (as in neka-nat/freecad-mcp): binds to 127.0.0.1 by default,
refuses requests carrying a browser ``Origin`` header or a non-XML content
type (CSRF / DNS rebinding), and optionally requires a bearer token.
"""

import contextlib
import hmac
import io
import json
import os
import threading
import traceback
from socketserver import ThreadingMixIn
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import FreeCAD
import FreeCADGui

from . import gui_dispatch
from .settings import load_settings

DEFAULT_PORT = 9876
_server = None
_thread = None
_NAMESPACE = {"FreeCAD": FreeCAD, "App": FreeCAD, "FreeCADGui": FreeCADGui, "Gui": FreeCADGui}


class _Handler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/", "/RPC2")

    def do_POST(self):
        reason = None
        if self.headers.get("Origin") is not None:
            reason = (403, "requests from web pages are not accepted")
        elif (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() not in ("text/xml", "application/xml"):
            reason = (415, "XML-RPC requires Content-Type text/xml")
        else:
            token = self.server.token
            if token:
                got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                if not hmac.compare_digest(got, token):
                    reason = (401, "missing or wrong auth token")
        if reason is None:
            return super().do_POST()
        body = reason[1].encode()
        self.send_response(reason[0])
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


def _json_safe(obj):
    return json.loads(json.dumps(obj, default=str))


class DrawingRPC:
    def ping(self):
        return True

    def get_rpc_status(self):
        from fd_drawing.ops import VERSION

        return {"addon": "FreeDrawingMCP", "version": VERSION, "freecad": ".".join(FreeCAD.Version()[:3]),
                "port": _server.server_address[1] if _server else None, "gui": True}

    def drawing_ops(self, steps):
        from fd_drawing import ops

        def run():
            out = []
            for st in steps:
                out.append(ops.run(st["op"], st.get("args") or {}))
            return out

        try:
            return {"results": _json_safe(gui_dispatch.dispatch(run, timeout=900, name="drawing_ops"))}
        except Exception as e:
            return {"error": str(e).splitlines()[0], "traceback": str(e)}

    def execute_code(self, code):
        buf = io.StringIO()

        def run():
            with contextlib.redirect_stdout(buf):
                exec(code, _NAMESPACE)
            return True

        try:
            gui_dispatch.dispatch(run, timeout=300, name="execute_code")
            return {"success": True, "output": buf.getvalue()}
        except Exception as e:
            return {"success": False, "error": str(e), "output": buf.getvalue()}


def start(port=None):
    global _server, _thread
    if _server is not None:
        return f"Free Drawing RPC already running on port {_server.server_address[1]}"
    settings = load_settings()
    port = int(port or settings.get("port", DEFAULT_PORT))
    host = "0.0.0.0" if settings.get("remote_enabled") else "127.0.0.1"
    srv = _Server((host, port), requestHandler=_Handler, allow_none=True, logRequests=False)
    srv.token = settings.get("auth_token") or ""
    srv.register_instance(DrawingRPC())
    gui_dispatch.init_waker()
    from PySide import QtCore

    QtCore.QTimer.singleShot(500, gui_dispatch.process)
    _thread = threading.Thread(target=srv.serve_forever, daemon=True)
    _thread.start()
    _server = srv
    return f"Free Drawing RPC started on {host}:{port} (PID {os.getpid()})"


def stop():
    global _server, _thread
    if _server is None:
        return "Free Drawing RPC was not running"
    srv = _server
    _server = None
    gui_dispatch.request_shutdown()
    gui_dispatch.cleanup_waker()
    threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
    return "Free Drawing RPC stopping"


def is_running():
    return _server is not None
