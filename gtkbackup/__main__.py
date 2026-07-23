"""Argument routing. Default -> GUI. --backup / --list-devices -> headless.

GTK (`gi`) is imported only on the GUI branch, so headless runs need no display.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="gtk-backup",
                                     description="Home-directory backup to an external drive.")
    parser.add_argument("--backup", action="store_true",
                        help="Run one backup headlessly, then exit.")
    parser.add_argument("--list-devices", action="store_true",
                        help="Print candidate backup targets and exit.")
    parser.add_argument("--trigger", default="cli",
                        help="Label recorded in history (cli|timer|gui).")
    args = parser.parse_args(argv)

    if args.list_devices:
        from .cli import list_devices
        return list_devices()

    if args.backup:
        from .cli import run_headless
        return run_headless(trigger=args.trigger)

    from .app import BackupApp        # GTK imported only here
    return BackupApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
