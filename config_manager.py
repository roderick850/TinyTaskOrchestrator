import json
import os
import sys

CONFIG_FILE = "playlist.json"

DEFAULT_SETTINGS = {
    "loop_mode": "once",
    "loop_count": 1,
    "loop_delay": 0,
    "hotkey": "f10",
    "window_geometry": "",
}


def _get_app_dir():
    """Return the directory where config should be saved.
    If running from a PyInstaller .exe, use APPDATA.
    Otherwise, use the script's own directory."""
    if getattr(sys, "frozen", False):
        # Running from a compiled .exe (PyInstaller)
        app_data = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TinyTaskOrchestrator")
        if not os.path.exists(app_data):
            os.makedirs(app_data)
        return app_data
    else:
        # Running from source
        return os.path.dirname(os.path.abspath(__file__))


def get_config_path():
    return os.path.join(_get_app_dir(), CONFIG_FILE)


def load_config():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Backward compatibility: old format was just a list
                    return {"playlist": data, "settings": DEFAULT_SETTINGS.copy()}
                return {
                    "playlist": data.get("playlist", []),
                    "settings": {**DEFAULT_SETTINGS, **data.get("settings", {})},
                }
        except Exception:
            return {"playlist": [], "settings": DEFAULT_SETTINGS.copy()}
    return {"playlist": [], "settings": DEFAULT_SETTINGS.copy()}


def save_config(playlist, settings):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"playlist": playlist, "settings": settings}, f, indent=2, ensure_ascii=False)
