# gtk-backup

A small GTK4 / libadwaita app (with a headless CLI twin) that mirrors your
home directory to an external drive using `rsync`, keeps a dated safety net of
changed/deleted files, and records history in SQLite. Built for CachyOS/Arch
but should run on any modern Linux with GTK4, libadwaita, and rsync.

## What it does

- **Mirror + safety net.** `rsync --delete` keeps
  `<drive>/backup/<hostname>/home/` an exact copy of your home folder. Anything
  about to be overwritten or deleted is first moved into
  `<drive>/backup/<hostname>/_changed/<date>/`, so nothing is lost silently —
  but you don't pay for full versioned snapshots.
- **Per-machine, not synced.** The backup is namespaced by hostname, so several
  machines can share one external drive without overwriting each other's mirror.
  Backups stay independent per machine; cross-machine sharing is left to your
  own remote storage (e.g. an sshfs mount), not this tool.
- **Safe by construction.** `-x` (one-file-system) plus an explicit exclude of
  `~/rjmstore` means network/remote mounts inside your home are never pulled
  into the backup. Verified against a real home tree: zero paths under the
  sshfs mount reach the transfer plan.
- **GUI:** pick a mounted drive (auto-detected, remembered by UUID), see the
  space check, last-backup status and history, and a live progress bar. Run a
  backup with one button; cancel mid-run.
- **Safe to unplug.** Every backup ends with a targeted `sync -f` so buffered
  writes are flushed before you can pull the drive. The GUI also has an eject
  button (⏏) that flushes, unmounts, and powers the drive off via `udisksctl`
  for a clean removal when you're in a hurry.
- **Headless:** the same engine runs from a daily `systemd --user` timer, and
  no-ops cleanly when the drive isn't plugged in.

## Requirements

- Python 3.11+ (developed on 3.14)
- `rsync`
- PyGObject with GTK 4 and libadwaita (`Adw` 1) — only for the GUI; the
  `--backup` / `--list-devices` paths need neither GTK nor a display.

On Arch/CachyOS: `sudo pacman -S rsync python-gobject gtk4 libadwaita udisks2`.

## Install (CachyOS / Arch)

```sh
git clone https://github.com/rjeffmyers/gtk-backup
cd gtk-backup
./install.sh
```

`install.sh` installs any missing dependencies (via pacman), copies the app
into `~/.local/share/gtkbackup`, generates a launcher at
`~/.local/bin/gtk-backup`, adds a **Home Backup** entry to the KDE menu, and
installs + enables the systemd `--user` timer. The clone can be deleted
afterward.

Flags: `--no-deps` (skip pacman), `--no-timer` (install but don't enable the
timer), `--enable-linger` (also back up while logged out), `--help`.

The backup itself is per-machine — namespaced by hostname on the drive — so you
can safely run the same drive across several machines without them clobbering
each other.

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
| On the drive       | `<mount>/backup/<hostname>/home/`, `<mount>/backup/<hostname>/_changed/<date>/` |

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
