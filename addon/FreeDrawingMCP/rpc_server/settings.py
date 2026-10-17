"""Addon settings stored in FreeCAD's user data directory."""

import json
import os

import FreeCAD

_FILE = "free_drawing_mcp_settings.json"
_DEFAULTS = {"port": 9876, "auto_start": False, "remote_enabled": False, "auth_token": ""}


def _path():
    return os.path.join(FreeCAD.getUserAppDataDir(), _FILE)


def load_settings():
    data = dict(_DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    return data


def save_settings(settings):
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
