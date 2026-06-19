import json
import os
import sys

CONFIG_FILE = "playlist.json"
ACTIVE_PROFILE_FILE = "active_profile.txt"


DEFAULT_SETTINGS = {
    "loop_mode": "once",
    "loop_count": 1,
    "loop_delay": 0,
    "hotkey": "f10",
    "window_geometry": "",
    # ── Mini Bar / UI settings ──
    "mini_bar_enabled": True,
    "mini_bar_geometry": "450x36",
    "mini_bar_pinned": True,
    "menu_bar_visible": True,
    # ── Group collapse state ──
    "collapsed_groups": [],
    # ── Window geometries (persisted positions/sizes) ──
    "macro_editor_geometry": "",
    "condition_editor_geometry": "",
    "script_editor_geometry": "",
}


def _get_app_dir():
    """Return the directory where config should be saved.
    If running from a PyInstaller .exe, use APPDATA.
    Otherwise, use the script's own directory."""
    import shutil

    if getattr(sys, "frozen", False):
        # Running from a compiled .exe (PyInstaller)
        app_data = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "AutoPulsar",
        )
        # ── Migration from old app name ──
        if not os.path.exists(app_data):
            old_dir = os.path.join(
                os.environ.get("APPDATA", os.path.expanduser("~")),
                "TinyTaskOrchestrator",
            )
            if os.path.isdir(old_dir):
                try:
                    shutil.copytree(old_dir, app_data)
                except Exception:
                    pass  # non-critical — user can copy manually
        if not os.path.exists(app_data):
            os.makedirs(app_data)
        return app_data
    else:
        # Running from source
        return os.path.dirname(os.path.abspath(__file__))


def get_config_path():
    return os.path.join(_get_app_dir(), CONFIG_FILE)


def get_profiles_dir():
    """Return the profiles directory path (creates if needed)."""
    profiles_dir = os.path.join(_get_app_dir(), "profiles")
    os.makedirs(profiles_dir, exist_ok=True)
    return profiles_dir


def _get_active_profile_path():
    """Return the file that stores the active profile name."""
    return os.path.join(_get_app_dir(), ACTIVE_PROFILE_FILE)


def get_active_profile_name():
    """Return the name of the currently active profile."""
    path = _get_active_profile_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                name = f.read().strip()
                if name:
                    return name
        except Exception:
            pass
    return "default"


def set_active_profile_name(name):
    """Save the active profile name."""
    path = _get_active_profile_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(name)


def list_profiles():
    """Return a sorted list of available profile names."""
    profiles_dir = get_profiles_dir()
    names = []
    if os.path.isdir(profiles_dir):
        for fname in os.listdir(profiles_dir):
            if fname.endswith(".json"):
                names.append(fname[:-5])  # strip .json
    return sorted(names)


def _profile_path(name):
    """Return the file path for a profile name."""
    return os.path.join(get_profiles_dir(), f"{name}.json")


def load_config(profile_name=None):
    """Load playlist + settings for the given profile (or active if None).
    If profile doesn't exist, returns defaults."""
    if profile_name is None:
        profile_name = get_active_profile_name()

    path = _profile_path(profile_name)

    # ── Migration: if legacy playlist.json exists and no profiles yet ──
    legacy_path = get_config_path()
    if not os.path.exists(path) and os.path.exists(legacy_path):
        _migrate_legacy_to_profile(legacy_path, profile_name)

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


def save_config(data, profile_name=None):
    """Save playlist + settings for the given profile (or active if None).

    Uses atomic write: data is written to a temp file first, then
    atomically replaces the target.  A backup copy (``.bak``) is
    kept from the previous successful save so that a crash or disk-
    full situation never leaves the profile file empty or truncated.
    """
    if profile_name is None:
        profile_name = get_active_profile_name()

    path = _profile_path(profile_name)
    dirname = os.path.dirname(path)

    # ── Temp file in the same directory (guarantees same filesystem → atomic rename) ──
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())          # force data to disk
    except Exception:
        # Clean up partial temp file if write failed
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    # ── Keep a backup of the previous good save ──
    if os.path.exists(path):
        bak_path = path + ".bak"
        try:
            os.replace(path, bak_path)
        except OSError:
            pass  # non-critical — we still have the temp file

    # ── Atomic rename (temp → real) ──
    try:
        os.replace(tmp_path, path)
    except OSError:
        # If rename fails, try to restore from backup
        if os.path.exists(path + ".bak"):
            try:
                os.replace(path + ".bak", path)
            except OSError:
                pass
        raise


def delete_profile(name):
    """Delete a profile file. Does nothing if it doesn't exist."""
    path = _profile_path(name)
    if os.path.exists(path):
        os.remove(path)


def rename_profile(old_name, new_name):
    """Rename a profile. Returns True on success, False if target exists."""
    old_path = _profile_path(old_name)
    new_path = _profile_path(new_name)
    if not os.path.exists(old_path):
        return False
    if os.path.exists(new_path):
        return False  # target already exists
    os.rename(old_path, new_path)
    # If this was the active profile, update the pointer
    if get_active_profile_name() == old_name:
        set_active_profile_name(new_name)
    return True


def clone_profile(source_name, target_name):
    """Clone a profile. Returns True on success, False if target exists."""
    source_path = _profile_path(source_name)
    target_path = _profile_path(target_name)
    if not os.path.exists(source_path):
        return False
    if os.path.exists(target_path):
        return False
    import shutil
    shutil.copy2(source_path, target_path)
    return True


def _migrate_legacy_to_profile(legacy_path, profile_name):
    """Move legacy playlist.json into the profiles directory as the default profile."""
    target_path = _profile_path(profile_name)
    profiles_dir = get_profiles_dir()
    try:
        import shutil
        shutil.copy2(legacy_path, target_path)
        # Don't delete legacy — keep as backup
    except Exception:
        pass
