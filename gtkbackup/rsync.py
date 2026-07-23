"""Pure rsync command construction and output parsing.

No side effects, no `gi`, no subprocess launch here — that lives in engine.py.
Keeping this module pure makes the load-bearing safety/correctness logic
(the argv and the exclusion flags) trivially unit-testable.
"""

from __future__ import annotations

import re
from pathlib import Path

# rsync exit codes we treat as success. 24 = "some files vanished before
# transfer", which is normal when backing up a live home directory.
OK_EXIT_CODES = (0, 24)


def build_argv(*, source: str, dest_home: str, changed_dir: str,
               exclude_file: str, log_file: str,
               dry_run: bool = False, stats: bool = True,
               progress: bool = True) -> list[str]:
    """Construct the rsync argv for a mirror-plus-safety-net backup.

    source:       directory to back up (e.g. /home/jeff)
    dest_home:    <mount>/backup/home   (the always-current mirror)
    changed_dir:  <mount>/backup/_changed/<date>  (safety net for
                  overwritten/deleted files)

    A real backup uses stats=True, progress=True (live line + final counts).
    A preflight uses dry_run=True, stats=True, progress=False.
    """
    argv = [
        "rsync",
        "-aHAX",              # archive + hardlinks + ACLs + xattrs
        "--numeric-ids",      # keep literal uid/gid
        "--human-readable",
        "-x",                 # one-file-system: never descend into nested mounts
        f"--exclude-from={exclude_file}",
        "--delete",           # mirror: prune files gone from source ...
        "--delete-excluded",  # ... and files newly matched by excludes
        "--backup",           # ... but first move casualties aside:
        f"--backup-dir={changed_dir}",
    ]
    if dry_run:
        argv.append("--dry-run")
    if stats:
        argv.append("--stats")
    if progress:
        argv.append("--info=progress2")
    argv += [
        f"--log-file={log_file}",
        "--log-file-format=%o %f %l",
    ]
    # Trailing slash on source copies its *contents* into dest_home.
    argv += [source.rstrip("/") + "/", dest_home.rstrip("/") + "/"]
    return argv


# --- progress parsing (--info=progress2) -----------------------------------
# Example line (updated in place with '\r'):
#     1,234,567,890  55%   85.32MB/s    0:03:12
# Rate prefixes are mixed-case in rsync output ("kB/s", "MB/s", "GB/s").
_PROGRESS = re.compile(
    r"([\d,]+)\s+(\d+)%\s+([\d.]+[kKMGTP]?B/s)\s+(\d+:\d+:\d+)"
)


def parse_progress(chunk: str) -> dict | None:
    """Return the last progress datapoint in a chunk of output, or None."""
    last = None
    for m in _PROGRESS.finditer(chunk):
        last = m
    if last is None:
        return None
    return {
        "bytes": int(last.group(1).replace(",", "")),
        "pct": int(last.group(2)),
        "rate": last.group(3),
        "eta": last.group(4),
    }


# --- stats parsing (--stats tail block, or the final progress2 summary) -----
_STATS_PATTERNS = {
    "files_transferred": re.compile(r"Number of regular files transferred:\s*([\d,]+)"),
    "files_deleted": re.compile(r"Number of deleted files:\s*([\d,]+)"),
    "source_total_bytes": re.compile(r"Total file size:\s*([\d,]+)"),
    "bytes_transferred": re.compile(r"Total transferred file size:\s*([\d,]+)"),
}


def parse_stats(text: str) -> dict:
    """Extract numeric fields from an rsync --stats block. Missing -> absent."""
    out: dict[str, int] = {}
    for key, pat in _STATS_PATTERNS.items():
        m = pat.search(text)
        if m:
            out[key] = int(m.group(1).replace(",", ""))
    return out


def split_stream(buf: str) -> tuple[list[str], str]:
    """Split a stdout buffer on both '\\r' and '\\n'.

    Returns (complete_pieces, remainder). progress2 separates live updates
    with '\\r', so a plain line-reader would stall until end of transfer.
    """
    pieces = re.split(r"[\r\n]", buf)
    return pieces[:-1], pieces[-1]


def dry_run_will_transfer(source: str, dest_home: str, changed_dir: str,
                          exclude_file: str, log_file: str) -> list[str]:
    """Convenience argv for a preflight (dry-run + stats, no live progress)."""
    return build_argv(source=source, dest_home=dest_home, changed_dir=changed_dir,
                       exclude_file=exclude_file, log_file=log_file,
                       dry_run=True, stats=True, progress=False)
