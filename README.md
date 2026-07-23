# gtk-backup

A small GTK4 / libadwaita app (with a headless CLI twin) that mirrors your
home directory to an external drive using `rsync`, keeps a dated safety net of
changed/deleted files, and records history in SQLite. Built for CachyOS/Arch
but should run on any modern Linux with GTK4, libadwaita, and rsync.

## What it does

- **Mirror + safety net.** `rsync --delete` keeps `<drive>/backup/home/` an
  exact copy of your home folder. Anything about to be overwritten or deleted
  is first moved into `<drive>/backup/_changed/<date>/`, so nothing is lost
  silently — but you don't pay for full versioned snapshots.
- **Safe by construction.** `-x` (one-file-system) plus an explicit exclude of
  `~/rjmstore` means network/remote mounts inside your home are never pulled
  into the backup. Verified against a real home tree: zero paths under the
  sshfs mount reach the transfer plan.
- **GUI:** pick a mounted drive (auto-detected, remembered by UUID), see the
  space check, last-backup status and history, and a live progress bar. Run a
  backup with one button; cancel mid-run.
- **Headless:** the same engine runs from a daily `systemd --user` timer, and
  no-ops cleanly when the drive isn't plugged in.

## Requirements

- Python 3.11+ (developed on 3.14)
- `rsync`
- PyGObject with GTK 4 and libadwaita (`Adw` 1) — only for the GUI; the
  `--backup` / `--list-devices` paths need neither GTK nor a display.

On Arch/CachyOS: `sudo pacman -S rsync python-gobject gtk4 libadwaita`.

## Layout

```
gtkbackup/
  __main__.py   argument routing (GUI vs --backup vs --list-devices)
  config.py     XDG paths, seeded excludes.txt        (no gi)
  db.py         SQLite schema + queries               (no gi)
  devices.py    findmnt-based target discovery        (no gi)
  sizing.py     source-size estimate + space check    (no gi)
  rsync.py      pure argv builder + output parsers     (no gi)
  engine.py     prepare/run_blocking (CLI) + AsyncRunner (GUI)
  cli.py        headless --backup / --list-devices    (no gi)
  app.py        Adw.Application + BackupWindow         (gi)
  widgets.py    device/history row helpers            (gi)
  data/         .desktop, systemd units, default excludes
```

Everything except `app.py` and `widgets.py` is free of any `gi` import, so the
timer path is display-independent.

## Files it creates

| Purpose            | Path                                   |
|--------------------|----------------------------------------|
| SQLite history     | `~/.local/share/gtkbackup/backups.db`  |
| Exclude patterns   | `~/.config/gtkbackup/excludes.txt`     |
| rsync logs         | `~/.local/state/gtkbackup/logs/`       |
| On the drive       | `<mount>/backup/home/`, `<mount>/backup/_changed/<date>/` |

## Usage

```sh
gtk-backup                 # launch the GUI
gtk-backup --list-devices  # show candidate target drives
gtk-backup --backup        # run one backup headlessly to the saved target
```

Edit `~/.config/gtkbackup/excludes.txt` to skip caches, VM images, etc. — it's
plain `rsync --exclude-from` format; leading `/` anchors to your home root.

## Daily automation (systemd user timer)

```sh
cp gtkbackup/data/systemd/gtk-backup.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gtk-backup.timer
loginctl enable-linger "$USER"   # optional: run even when logged out
```

Runs daily at 02:00 (with a random ≤15 min delay), catches up if the machine
was off, and skips cleanly when the drive is absent. Check it with:

```sh
systemctl --user list-timers gtk-backup.timer
journalctl --user -u gtk-backup
```

## Notes

This protects against drive failure and accidental deletion, but it's a single
external drive next to the machine — not an off-site backup. For irreplaceable
data, keep a second rotated drive or an off-site copy as well.
