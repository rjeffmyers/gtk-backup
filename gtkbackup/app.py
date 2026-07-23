"""GTK4 + libadwaita UI. This module (and widgets.py) are the only ones that
import `gi`. Long-running rsync is driven by engine.AsyncRunner on the GLib
main loop, so the UI never blocks.
"""

from __future__ import annotations

import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import config, db, devices, engine, sizing, widgets  # noqa: E402

APP_ID = "org.jeff.GtkBackup"


class BackupWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Home Backup")
        self.set_default_size(560, 720)

        self.conn = db.connect()
        db.reconcile_stale(self.conn)
        self.devices: list[devices.Device] = []
        self.runner: engine.AsyncRunner | None = None
        self.active_prep = None

        toast_overlay = Adw.ToastOverlay()
        self.toasts = toast_overlay
        self.set_content(toast_overlay)

        toolbar = Adw.ToolbarView()
        toast_overlay.set_child(toolbar)

        header = Adw.HeaderBar()
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Edit excludes…", "win.edit-excludes")
        menu.append("Open backup folder", "win.open-target")
        menu.append("About", "win.about")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)
        toolbar.add_top_bar(header)

        clamp = Adw.Clamp(maximum_size=620, margin_top=18, margin_bottom=18,
                          margin_start=12, margin_end=12)
        toolbar.set_content(clamp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(box)

        box.append(self._build_status_group())
        box.append(self._build_target_group())
        box.append(self._build_action_area())
        box.append(self._build_history_group())

        self._install_actions()
        self._connect_volume_monitor()
        self.refresh_devices()
        self.refresh_status()
        self.refresh_history()

    # --- UI construction ---------------------------------------------------

    def _build_status_group(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title="Status")
        self.last_row = Adw.ActionRow(title="Last backup", subtitle="—")
        self.home_row = Adw.ActionRow(title="Home folder", subtitle="—")
        g.add(self.last_row)
        g.add(self.home_row)
        return g

    def _build_target_group(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title="Backup target")
        self.device_model = Gtk.StringList()
        self.device_row = Adw.ComboRow(title="Device", model=self.device_model)
        self.device_row.connect("notify::selected", self._on_device_changed)
        g.add(self.device_row)
        self.space_row = Adw.ActionRow(title="Space check", subtitle="—")
        g.add(self.space_row)
        return g

    def _build_action_area(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.backup_btn = Gtk.Button(label="Backup Now")
        self.backup_btn.add_css_class("suggested-action")
        self.backup_btn.add_css_class("pill")
        self.backup_btn.connect("clicked", self._on_backup_clicked)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.add_css_class("destructive-action")
        self.cancel_btn.add_css_class("pill")
        self.cancel_btn.connect("clicked", self._on_cancel_clicked)
        self.cancel_btn.set_visible(False)
        btn_row.append(self.backup_btn)
        btn_row.append(self.cancel_btn)
        box.append(btn_row)

        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        box.append(self.progress)
        self.progress_label = Gtk.Label(halign=Gtk.Align.START, visible=False)
        self.progress_label.add_css_class("dim-label")
        self.progress_label.add_css_class("caption")
        box.append(self.progress_label)
        return box

    def _build_history_group(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title="History")
        scroll = Gtk.ScrolledWindow(min_content_height=220, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.history_list = Gtk.ListBox()
        self.history_list.add_css_class("boxed-list")
        self.history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self.history_list)
        g.add(scroll)
        return g

    # --- actions / menu ----------------------------------------------------

    def _install_actions(self):
        for name, handler in (
            ("edit-excludes", self._act_edit_excludes),
            ("open-target", self._act_open_target),
            ("about", self._act_about),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", handler)
            self.add_action(act)

    def _act_edit_excludes(self, *_):
        path = config.ensure_excludes()
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(str(path)), None)

    def _act_open_target(self, *_):
        dev = self._selected_device()
        if dev:
            target = str(config.dest_root(dev.target))
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(target), None)

    def _act_about(self, *_):
        about = Adw.AboutWindow(
            transient_for=self, application_name="Home Backup",
            application_icon="drive-harddisk", version="0.1.0",
            developer_name="Jeff", comments="rsync mirror of your home folder "
            "to an external drive, with a dated safety net for changed files.")
        about.present()

    # --- device handling ---------------------------------------------------

    def _connect_volume_monitor(self):
        self._vm = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed"):
            self._vm.connect(sig, lambda *a: self.refresh_devices())

    def refresh_devices(self):
        self.devices = devices.list_targets()
        # rebuild the StringList
        while self.device_model.get_n_items():
            self.device_model.remove(0)
        for d in self.devices:
            self.device_model.append(widgets.device_label(d))
        # restore saved selection by UUID
        saved = db.get_config(self.conn, "last_target_uuid")
        idx = next((i for i, d in enumerate(self.devices) if d.uuid == saved), -1)
        if idx >= 0:
            self.device_row.set_selected(idx)
        self._update_space_row()
        self.backup_btn.set_sensitive(bool(self.devices) and self.runner is None)
        if not self.devices:
            self.space_row.set_subtitle("No backup drive mounted — plug one in.")

    def _selected_device(self) -> devices.Device | None:
        i = self.device_row.get_selected()
        if 0 <= i < len(self.devices):
            return self.devices[i]
        return None

    def _on_device_changed(self, *_):
        dev = self._selected_device()
        if dev and dev.uuid:
            db.set_config(self.conn, "last_target_uuid", dev.uuid)
        self._update_space_row()

    def _update_space_row(self):
        dev = self._selected_device()
        if not dev:
            return
        try:
            sc = sizing.check(self.conn, dev.free_bytes())
        except Exception as e:  # e.g. du failure on first run
            self.space_row.set_subtitle(f"Could not estimate size: {e}")
            return
        verdict = "fits ✓" if sc.fits else "NOT enough space ✗"
        est = "~" if sc.source_is_estimate else ""
        self.space_row.set_subtitle(
            f"Home {est}{sizing.human(sc.source_bytes)} · "
            f"target free {sizing.human(sc.free_bytes)} · {verdict}")
        self.space_row.set_css_classes([] if sc.fits else ["error"])

    # --- status / history --------------------------------------------------

    def refresh_status(self):
        row = db.last_successful(self.conn)
        if row:
            size = sizing.human(row["bytes_transferred"])
            self.last_row.set_subtitle(
                f"{row['finished_at']} · {row['status']} · {size}")
        else:
            self.last_row.set_subtitle("never")
        cached = sizing.cached_source_bytes(self.conn)
        self.home_row.set_subtitle(
            f"~{sizing.human(cached)}" if cached else "size not measured yet")

    def refresh_history(self):
        child = self.history_list.get_first_child()
        while child:
            self.history_list.remove(child)
            child = self.history_list.get_first_child()
        rows = db.history(self.conn, limit=100)
        if not rows:
            placeholder = Adw.ActionRow(title="No backups yet",
                                        subtitle="Run one with “Backup Now”.")
            self.history_list.append(placeholder)
            return
        for r in rows:
            row = widgets.history_row(r)
            if getattr(row, "_log_button", None):
                row._log_button.connect("clicked", self._open_log)
            self.history_list.append(row)

    def _open_log(self, btn):
        Gio.AppInfo.launch_default_for_uri(
            GLib.filename_to_uri(btn._log_path), None)

    # --- backup run --------------------------------------------------------

    def _on_backup_clicked(self, *_):
        dev = self._selected_device()
        if not dev:
            return
        try:
            sc = sizing.check(self.conn, dev.free_bytes())
        except Exception as e:
            self.toasts.add_toast(Adw.Toast(title=f"Size check failed: {e}"))
            return
        if not sc.fits:
            self.toasts.add_toast(Adw.Toast(
                title=f"Not enough space: need ~{sizing.human(sc.required_bytes)}, "
                f"have {sizing.human(sc.free_bytes)}."))
            return
        if dev.uuid:
            db.set_config(self.conn, "last_target_uuid", dev.uuid)

        # Guard against a concurrent timer/CLI run to the same destination.
        try:
            self._lock = engine.acquire_lock()
        except engine.AlreadyRunning:
            self.toasts.add_toast(Adw.Toast(
                title="A backup is already running (timer or another window)."))
            return

        self.active_prep = engine.prepare(self.conn, dev, trigger="gui")
        self.runner = engine.AsyncRunner()
        self._set_running(True)
        self.runner.start(self.active_prep, self._on_progress, self._on_done)

    def _set_running(self, running: bool):
        self.backup_btn.set_visible(not running)
        self.backup_btn.set_sensitive(not running and bool(self.devices))
        self.cancel_btn.set_visible(running)
        self.device_row.set_sensitive(not running)
        self.progress.set_visible(running)
        self.progress_label.set_visible(running)
        if running:
            self.progress.set_fraction(0.0)
            self.progress.set_text("Starting…")
            self.progress_label.set_text("Preparing file list…")

    def _on_progress(self, p: dict):
        self.progress.set_fraction(p["pct"] / 100.0)
        self.progress.set_text(f"{p['pct']}% · {p['rate']} · ETA {p['eta']}")

    def _on_done(self, exit_code: int, stdout_text: str):
        status = engine.finalize(self.conn, self.active_prep, exit_code, stdout_text)
        engine.release_lock(getattr(self, "_lock", None))
        self._lock = None
        self.runner = None
        self._set_running(False)
        self.refresh_status()
        self.refresh_history()
        self._update_space_row()
        nice = {"success": "Backup complete", "partial": "Backup finished (some files skipped)",
                "failed": "Backup failed", "cancelled": "Backup cancelled"}
        self.toasts.add_toast(Adw.Toast(title=nice.get(status, status)))

    def _on_cancel_clicked(self, *_):
        if self.runner:
            self.runner.cancel()
            # _on_done will fire from wait_check with a nonzero code; mark it.


class BackupApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win: BackupWindow | None = None

    def do_activate(self):
        if not self.win:
            self.win = BackupWindow(self)
        self.win.present()
