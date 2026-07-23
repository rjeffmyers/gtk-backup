"""Source-size estimation and the go/no-go space check.

`du` over a 200GB+ home is slow, so we prefer the size cached from the last
run's rsync --stats. First run (no cache) computes `du -sxb` with excludes;
the GUI can also request an accurate `rsync --dry-run --stats` delta.
No `gi` import.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from . import config, db

# Require this much headroom over the estimate before allowing a backup.
HEADROOM = 1.05


@dataclass
class SpaceCheck:
    source_bytes: int          # estimated size of what will end up on target
    free_bytes: int            # exact free space on target
    required_bytes: int        # source_bytes * HEADROOM
    fits: bool
    source_is_estimate: bool   # True unless derived from a --dry-run delta


def cached_source_bytes(conn) -> int | None:
    val = db.get_config(conn, "cached_source_bytes")
    return int(val) if val else None


def store_source_bytes(conn, total: int) -> None:
    db.set_config(conn, "cached_source_bytes", str(total))
    db.set_config(conn, "cached_source_at", str(int(time.time())))


def du_source_bytes(source: str | None = None) -> int:
    """Exact apparent size of the source, staying on one filesystem (-x),
    honoring the exclude file so the number matches what rsync will copy."""
    source = source or str(config.source_home())
    excludes = config.ensure_excludes()
    args = ["du", "-sxb"]
    # Feed exclude *names* to du where we can; du's --exclude is glob-based.
    for line in excludes.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        args.append(f"--exclude={line.lstrip('/')}")
    args.append(source)
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return int(out.split("\t", 1)[0])


def check(conn, device_free_bytes: int, source: str | None = None,
          delta_bytes: int | None = None) -> SpaceCheck:
    """Build a SpaceCheck. Uses `delta_bytes` (from a dry-run) if given,
    else the cached full size, else a fresh du."""
    if delta_bytes is not None:
        est = delta_bytes
        is_estimate = False
    else:
        est = cached_source_bytes(conn)
        is_estimate = True
        if est is None:
            est = du_source_bytes(source)
            store_source_bytes(conn, est)
    required = int(est * HEADROOM)
    return SpaceCheck(
        source_bytes=est,
        free_bytes=device_free_bytes,
        required_bytes=required,
        fits=required <= device_free_bytes,
        source_is_estimate=is_estimate,
    )


def human(n: int | None) -> str:
    if n is None:
        return "—"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} EB"
