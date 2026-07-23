"""Backup execution — the shared core for GUI and headless paths.

`prepare()` and `run_blocking()` are pure Python (no `gi`) so the systemd
timer path works headless. `AsyncRunner` lazily imports Gio/GLib and is only
touched by the GTK app.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, db, rsync
from .devices import Device


class AlreadyRunning(Exception):
    """Another gtk-backup process already holds the run lock."""


def _lock_file() -> Path:
    return config.state_dir() / "backup.lock"


def acquire_lock():
    """Take an exclusive, non-blocking lock shared by GUI and timer.

    Returns an open file handle that must be kept alive for the duration of
    the run (the lock is held as long as the handle is open / process lives).
    Raises AlreadyRunning if another process holds it.
    """
    config.ensure_dirs()
    f = open(_lock_file(), "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise AlreadyRunning()
    f.write(str(os.getpid()))
    f.flush()
    return f


def release_lock(f) -> None:
    if not f:
        return
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


@dataclass
class Prepared:
    argv: list[str]
    dest_home: Path
    changed_dir: Path
    log_path: Path
    run_id: int
    device: Device
    started_monotonic: float


class NoDeviceError(Exception):
    """Raised when the requested target UUID is not currently mounted."""


def _timestamp() -> str:
    # datetime.now() is fine in the app/CLI process (unlike workflow scripts).
    return datetime.now().strftime("%Y-%m-%dT%H%M%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def prepare(conn, device: Device, trigger: str,
            source: str | None = None, mode: str = "mirror") -> Prepared:
    """Create target dirs, seed excludes, build argv, open a DB run row."""
    source = source or str(config.source_home())
    config.ensure_dirs()
    exclude_file = config.ensure_excludes()

    dest_home = config.home_dest(device.target)
    changed_dir = config.changed_dest(device.target, _today())
    dest_home.mkdir(parents=True, exist_ok=True)
    changed_dir.parent.mkdir(parents=True, exist_ok=True)

    ts = _timestamp()
    log_path = config.log_dir() / f"{ts}.log"

    argv = rsync.build_argv(
        source=source,
        dest_home=str(dest_home),
        changed_dir=str(changed_dir),
        exclude_file=str(exclude_file),
        log_file=str(log_path),
    )

    device_id = db.upsert_device(conn, device.uuid, device.label,
                                 device.fstype, device.target)
    run_id = db.start_run(conn, device_id, mode, trigger,
                          started_at=datetime.now().isoformat(timespec="seconds"),
                          log_path=str(log_path))
    return Prepared(argv=argv, dest_home=dest_home, changed_dir=changed_dir,
                    log_path=log_path, run_id=run_id, device=device,
                    started_monotonic=time.monotonic())


def _classify(exit_code: int) -> str:
    if exit_code == 0:
        return "success"
    if exit_code in rsync.OK_EXIT_CODES:      # 24: files vanished mid-run
        return "partial"
    return "failed"


def finalize(conn, prep: Prepared, exit_code: int, stdout_text: str,
             error_summary: str | None = None) -> str:
    """Parse stats, update the DB run row, cache source size, flush the drive.
    Returns status."""
    status = _classify(exit_code)
    stats = rsync.parse_stats(stdout_text)
    duration = time.monotonic() - prep.started_monotonic
    db.finish_run(conn, prep.run_id,
                  finished_at=datetime.now().isoformat(timespec="seconds"),
                  duration_secs=duration, status=status, exit_status=exit_code,
                  stats=stats, error_summary=error_summary)
    if status in ("success", "partial"):
        if stats.get("source_total_bytes"):
            from . import sizing
            sizing.store_source_bytes(conn, stats["source_total_bytes"])
        # Flush buffered writes so the drive is safe to unplug even if the
        # user pulls it right after the backup finishes.
        flush_target(prep.dest_home)
    return status


def flush_target(path) -> None:
    """Flush the filesystem containing `path` to disk (sync -f, targeted).

    Falls back to a global sync if `sync -f` isn't available. Best-effort:
    failures are swallowed so a backup is never reported failed over a flush.
    """
    try:
        subprocess.run(["sync", "-f", str(path)], check=False, timeout=180)
    except Exception:
        try:
            subprocess.run(["sync"], check=False, timeout=180)
        except Exception:
            pass


def safe_remove(device: Device) -> tuple[bool, str]:
    """Flush, unmount, and power off a removable drive for safe unplugging.

    Returns (ok, human_message). Uses udisksctl so no root is required for a
    user-mounted removable drive.
    """
    subprocess.run(["sync"], check=False)
    block = device.source           # e.g. /dev/sdc1
    if not block.startswith("/dev/"):
        return False, f"Don't know the block device for {device.name}."

    umount = subprocess.run(["udisksctl", "unmount", "-b", block],
                            capture_output=True, text=True)
    if umount.returncode != 0:
        msg = (umount.stderr or umount.stdout).strip()
        return False, f"Couldn't unmount {device.name}: {msg}"

    # Power off the whole disk (parent of the partition), if we can find it.
    parent = subprocess.run(["lsblk", "-no", "pkname", block],
                            capture_output=True, text=True).stdout.strip().splitlines()
    if parent:
        disk = f"/dev/{parent[0].strip()}"
        off = subprocess.run(["udisksctl", "power-off", "-b", disk],
                             capture_output=True, text=True)
        if off.returncode == 0:
            return True, f"{device.name} unmounted and powered off — safe to unplug."
    return True, f"{device.name} unmounted — safe to unplug."


def run_blocking(conn, prep: Prepared, on_progress=None) -> str:
    """Run rsync synchronously (CLI/timer path). Returns final status string."""
    proc = subprocess.Popen(
        prep.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    captured: list[str] = []
    buf = ""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        captured.append(chunk)
        buf += chunk
        pieces, buf = rsync.split_stream(buf)
        if on_progress:
            for piece in pieces:
                p = rsync.parse_progress(piece)
                if p:
                    on_progress(p)
    proc.wait()
    return finalize(conn, prep, proc.returncode, "".join(captured))


# --- GUI async runner (imports gi lazily) ----------------------------------

class AsyncRunner:
    """Drives rsync via Gio.Subprocess on the GLib main loop.

    read_bytes_async (not read_line_async) because --info=progress2 separates
    updates with '\\r'. Progress and completion are delivered via callbacks
    that already run on the main thread.
    """

    def __init__(self):
        self._proc = None
        self._stream = None
        self._buf = ""
        self._captured: list[str] = []
        self._on_progress = None
        self._on_done = None

    def start(self, prep: Prepared, on_progress, on_done):
        from gi.repository import Gio, GLib  # lazy: GUI only
        self._Gio, self._GLib = Gio, GLib
        self._on_progress, self._on_done = on_progress, on_done
        self._prep = prep
        self._proc = Gio.Subprocess.new(
            prep.argv,
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE,
        )
        self._stream = self._proc.get_stdout_pipe()
        self._pump()
        self._proc.wait_check_async(None, self._on_wait)

    def _pump(self):
        self._stream.read_bytes_async(
            65536, self._GLib.PRIORITY_DEFAULT, None, self._on_read)

    def _on_read(self, stream, res):
        try:
            data = stream.read_bytes_finish(res)
        except Exception:
            return
        if data is None or data.get_size() == 0:
            return  # EOF; completion handled by _on_wait
        text = data.get_data().decode("utf-8", "replace")
        self._captured.append(text)
        self._buf += text
        pieces, self._buf = rsync.split_stream(self._buf)
        for piece in pieces:
            p = rsync.parse_progress(piece)
            if p and self._on_progress:
                self._on_progress(p)
        self._pump()

    def _on_wait(self, proc, res):
        try:
            proc.wait_check_finish(res)
            code = 0
        except Exception:
            code = proc.get_exit_status() if proc.get_if_exited() else 1
        self._on_done(code, "".join(self._captured))

    def cancel(self):
        if self._proc:
            self._proc.force_exit()
