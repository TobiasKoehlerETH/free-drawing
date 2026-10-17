import os as _os
import sys as _sys

try:
    _addon_dir = _os.path.dirname(_os.path.abspath(__file__))
except NameError:  # FreeCAD execs InitGui.py without __file__
    import inspect as _inspect

    _addon_dir = _os.path.dirname(_os.path.abspath(_inspect.getfile(_inspect.currentframe())))
if _addon_dir not in _sys.path:
    _sys.path.insert(0, _addon_dir)


class _StartCmd:
    def GetResources(self):
        return {"MenuText": "Start Drawing RPC", "ToolTip": "Start the XML-RPC server for free-drawing-mcp"}

    def Activated(self):
        from rpc_server import server

        FreeCAD.Console.PrintMessage(server.start() + "\n")

    def IsActive(self):
        return True


class _StopCmd:
    def GetResources(self):
        return {"MenuText": "Stop Drawing RPC", "ToolTip": "Stop the XML-RPC server"}

    def Activated(self):
        from rpc_server import server

        FreeCAD.Console.PrintMessage(server.stop() + "\n")

    def IsActive(self):
        return True


class _AutoStartCmd:
    def GetResources(self):
        from rpc_server.settings import load_settings

        return {"MenuText": "Auto-start Drawing RPC", "ToolTip": "Start the RPC server when FreeCAD starts",
                "Checkable": bool(load_settings().get("auto_start"))}

    def Activated(self, checked=0):
        from rpc_server.settings import load_settings, save_settings

        s = load_settings()
        s["auto_start"] = bool(checked)
        save_settings(s)

    def IsActive(self):
        return True


FreeCADGui.addCommand("FreeDrawing_StartRPC", _StartCmd())
FreeCADGui.addCommand("FreeDrawing_StopRPC", _StopCmd())
FreeCADGui.addCommand("FreeDrawing_AutoStart", _AutoStartCmd())


class FreeDrawingMCPWorkbench(Workbench):  # noqa: F821 (provided by FreeCAD)
    MenuText = "Free Drawing MCP"
    ToolTip = "Automatic ISO drawings driven by AI agents over MCP"

    def Initialize(self):
        cmds = ["FreeDrawing_StartRPC", "FreeDrawing_StopRPC", "FreeDrawing_AutoStart"]
        self.appendToolbar("Free Drawing MCP", cmds)
        self.appendMenu("Free Drawing MCP", cmds)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(FreeDrawingMCPWorkbench())


def _auto_start():
    try:
        from rpc_server import server
        from rpc_server.settings import load_settings

        if load_settings().get("auto_start"):
            FreeCAD.Console.PrintMessage("[FreeDrawingMCP] " + server.start() + "\n")
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[FreeDrawingMCP] auto-start failed: {e}\n")


from PySide import QtCore  # noqa: E402

QtCore.QTimer.singleShot(0, _auto_start)
