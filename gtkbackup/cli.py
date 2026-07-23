"""Headless entry points: --backup (timer/manual) and --list-devices.

No `gi` import — this is what the systemd user timer runs.
"""

from __future__ import annotations

import sys

from . import config, db, devices, engine, sizing


def _pick_device(conn) -> devices.Device | None:
    """Resolve the saved target UUID to a currently-mounted device.

    Falls back to the sole candidate if exactly one is mounted and none saved.
    """
    saved = db.get_config(conn, "last_target_uuid")
    if saved:
        return devices.resolve_uuid(saved)
    candidates = devices.list_targets()
    return candidates[0] if len(candidates) == 1 else None


def run_headless(trigger: str = "cli") -> int:
    """Run one backup to the saved target. Exit 0 (noop) if it isn't mounted.

    Timer-friendly: an absent removable drive is a clean skip, not a failure.
    """
    conn = db.connect()
    db.reconcile_stale(conn)

    device = _pick_device(conn)
    if device is None:
        saved = db.get_config(conn, "last_target_uuid")
        if saved:
            print(f"gtkbackup: target {saved} not mounted; skipping.", file=sys.stderr)
        else:
            print("gtkbackup: no backup target configured or mounted; skipping.",
                  file=sys.stderr)
        return 0  # success-noop

    # Remember the choice for next time (e.g. first manual --backup).
    if device.uuid:
        db.set_config(conn, "last_target_uuid", device.uuid)

    # Space guard using cached/du estimate (dry-run delta not computed here).
    sc = sizing.check(conn, device.free_bytes())
    if not sc.fits:
        print(f"gtkbackup: not enough space on {device.name}: need "
              f"~{sizing.human(sc.required_bytes)}, have "
              f"{sizing.human(sc.free_bytes)}.", file=sys.stderr)
        return 1

    # Refuse to run concurrently with another gtk-backup (GUI or timer).
    try:
        lock = engine.acquire_lock()
    except engine.AlreadyRunning:
        print("gtkbackup: another backup is already running; skipping.",
              file=sys.stderr)
        return 0

    try:
        prep = engine.prepare(conn, device, trigger=trigger)

        def _tick(p):
            print(f"\r{p['pct']:3d}%  {p['rate']}  ETA {p['eta']}",
                  end="", file=sys.stderr)

        status = engine.run_blocking(conn, prep, on_progress=_tick)
    finally:
        engine.release_lock(lock)
    print(file=sys.stderr)  # newline after progress
    print(f"gtkbackup: {status} — target {device.name}, log {prep.log_path}")
    return 0 if status in ("success", "partial") else 1


def list_devices() -> int:
    targets = devices.list_targets()
    if not targets:
        print("No suitable backup targets currently mounted.")
        return 0
    for d in targets:
        print(f"{d.name:20s}  {d.fstype:6s}  free {sizing.human(d.free_bytes()):>10s}"
              f"  uuid={d.uuid}  at {d.target}")
    return 0
