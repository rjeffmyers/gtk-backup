"""XDG paths, the seeded exclude file, and small config helpers.

Deliberately free of any `gi`/GTK import so the headless timer path stays
display-independent. XDG dirs are resolved from the environment (with the
standard fallbacks) rather than via GLib for the same reason.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

APP_NAME = "gtkbackup"

# On the target drive the backup lives under backup/<hostname>/ so several
# machines can safely share one drive without overwriting each other's mirror
# (backups stay per-machine; shared files live on rjmstore, not here).
DEST_SUBDIR = "backup"


def hostname() -> str:
    """Short machine name, overridable for testing."""
    name = os.environ.get("GTKBACKUP_HOST") or socket.gethostname()
    return name.split(".")[0] or "unknown-host"


def dest_root(target: str | os.PathLike) -> Path:
    """<mount>/backup/<hostname>/ — this machine's private backup area."""
    return Path(target) / DEST_SUBDIR / hostname()


def home_dest(target: str | os.PathLike) -> Path:
    """<mount>/backup/<hostname>/home/ — the always-current mirror."""
    return dest_root(target) / "home"


def changed_dest(target: str | os.PathLike, date: str) -> Path:
    """<mount>/backup/<hostname>/_changed/<date>/ — safety net for casualties."""
    return dest_root(target) / "_changed" / date


def _xdg(env: str, default_rel: str) -> Path:
    val = os.environ.get(env)
    base = Path(val) if val else Path.home() / default_rel
    return base


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_NAME


def db_path() -> Path:
    return data_dir() / "backups.db"


def excludes_path() -> Path:
    return config_dir() / "excludes.txt"


def log_dir() -> Path:
    return state_dir() / "logs"


def source_home() -> Path:
    """The directory being backed up. Overridable for testing."""
    return Path(os.environ.get("GTKBACKUP_SOURCE", str(Path.home())))


# Seeded on first run. Per Jeff's choice: exclude mounts only; the rest are
# commented suggestions he can enable later by editing this plain-text file.
DEFAULT_EXCLUDES = """\
# gtkbackup exclude patterns (rsync --exclude-from format).
# Leading '/' anchors a pattern to the backup root (your home directory).
# Edit freely; lines starting with '#' are ignored.

# --- Nested / remote mounts (also blocked by rsync -x; kept explicit for safety) ---
/rjmstore/
/rjmstore

# --- Optional: uncomment any of these later to shrink the backup ---
# /.cache/
# /.local/share/Trash/
# /.thumbnails/
# /.local/share/libvirt/images/
# /.local/share/containers/
# **/node_modules/
# **/__pycache__/
# **/.venv/
"""


def ensure_dirs() -> None:
    for d in (config_dir(), data_dir(), state_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)


def ensure_excludes() -> Path:
    """Create the exclude file from the default template if it is missing."""
    path = excludes_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_EXCLUDES, encoding="utf-8")
    return path
