"""gtkbackup — a GTK4 home-directory backup app with a shared headless engine.

Non-GTK modules (config, db, devices, sizing, rsync, engine.run_blocking, cli)
never import `gi`, so the systemd timer path runs without a display.
"""

__version__ = "0.1.0"
