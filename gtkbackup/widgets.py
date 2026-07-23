"""Small GTK helpers for building device/history rows. GTK imported here."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import sizing

_STATUS_STYLE = {
    "success": ("Success", "success"),
    "partial": ("Partial", "warning"),
    "failed": ("Failed", "error"),
    "cancelled": ("Cancelled", "dim-label"),
    "running": ("Running", "accent"),
}


def status_pill(status: str) -> Gtk.Label:
    label_text, css = _STATUS_STYLE.get(status, (status, "dim-label"))
    lbl = Gtk.Label(label=label_text)
    lbl.add_css_class(css)
    lbl.add_css_class("caption-heading")
    return lbl


def history_row(run) -> Adw.ActionRow:
    """Build a one-line history row from a backup_run DB row."""
    started = run["started_at"] or "—"
    size = sizing.human(run["bytes_transferred"]) if run["bytes_transferred"] else "—"
    dur = run["duration_secs"]
    dur_txt = f"{int(dur // 60)}m {int(dur % 60)}s" if dur else "—"
    trig = run["trigger"]
    subtitle = f"{size} transferred · {dur_txt} · {trig}"

    row = Adw.ActionRow(title=started, subtitle=subtitle)
    row.add_suffix(status_pill(run["status"]))
    if run["rsync_log_path"]:
        btn = Gtk.Button(icon_name="text-x-generic-symbolic")
        btn.set_valign(Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.set_tooltip_text("View rsync log")
        btn._log_path = run["rsync_log_path"]
        row.add_suffix(btn)
        row._log_button = btn
    return row


def device_label(dev) -> str:
    return f"{dev.name} ({dev.fstype}) — {sizing.human(dev.free_bytes())} free"
