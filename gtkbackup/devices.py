"""Backup-target discovery via `findmnt --json`.

Pure/headless: no `gi` import. The GUI layer (app.py) wraps a
Gio.VolumeMonitor to know *when* to call list_targets() again, but the
authoritative snapshot always comes from findmnt here.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Filesystems we can meaningfully back up onto.
REAL_FS = {"ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "exfat", "ntfs", "vfat"}
# Where removable / auxiliary drives typically mount.
CANDIDATE_PREFIXES = ("/run/media/", "/media/", "/mnt/")


@dataclass(frozen=True)
class Device:
    target: str          # current mountpoint
    source: str          # e.g. /dev/sdc1
    fstype: str
    label: str | None
    uuid: str | None

    @property
    def name(self) -> str:
        return self.label or Path(self.target).name or self.source

    def free_bytes(self) -> int:
        """Exact free space for an unprivileged writer, in bytes."""
        s = os.statvfs(self.target)
        return s.f_bavail * s.f_frsize

    def size_bytes(self) -> int:
        s = os.statvfs(self.target)
        return s.f_blocks * s.f_frsize


def _findmnt() -> list[dict]:
    out = subprocess.run(
        ["findmnt", "-l", "--json", "-o",
         "TARGET,SOURCE,FSTYPE,LABEL,UUID,OPTIONS"],
        capture_output=True, text=True, check=True,
    ).stdout
    if not out.strip():
        return []
    return json.loads(out).get("filesystems", [])


def list_targets(home: str | None = None) -> list[Device]:
    """Return writable, mounted removable/auxiliary drives suitable as targets."""
    home = home or str(Path.home())
    devices: list[Device] = []
    for fs in _findmnt():
        target = fs.get("target") or ""
        if not target.startswith(CANDIDATE_PREFIXES):
            continue
        if target.startswith(home):        # never target something inside home
            continue
        if fs.get("fstype") not in REAL_FS:
            continue
        opts = (fs.get("options") or "").split(",")
        if "ro" in opts:                    # skip read-only mounts
            continue
        devices.append(Device(
            target=target,
            source=fs.get("source") or "",
            fstype=fs.get("fstype") or "",
            label=fs.get("label"),
            uuid=fs.get("uuid"),
        ))
    devices.sort(key=lambda d: d.name.lower())
    return devices


def resolve_uuid(uuid: str, home: str | None = None) -> Device | None:
    """Find the currently-mounted device with the given UUID, or None."""
    for d in list_targets(home):
        if d.uuid == uuid:
            return d
    return None
