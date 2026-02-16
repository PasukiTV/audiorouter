#!/usr/bin/env python3
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Pango, GLib, Gio

from .autostart import is_enabled as autostart_is_enabled, enable as autostart_enable, disable as autostart_disable


from .config import RULES_PATH, VSINKS_PATH, load_config, save_config
from . import pactl as pa
# Apply changes immediately (no "Apply" button)
from .core import apply_once
from .system_policy import install_system_sound_policy, remove_system_sound_policy, restart_pipewire_pulse, system_sound_policy_installed

APP_ID = "de.pasuki.audiorouter"

import re

DONATE_URL = "https://www.paypal.me/audiorouter"


def open_donate(_btn):
    launcher = Gtk.UriLauncher.new(DONATE_URL)
    launcher.launch(None)


def slugify_label(label: str) -> str:
    s = label.strip().lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "bus"

def make_bus_name(label: str, existing_names: set[str]) -> str:
    base = f"vsink.{slugify_label(label)}"
    name = base
    i = 2
    while name in existing_names:
        name = f"{base}-{i}"
        i += 1
    return name

def friendly_sink_list():
    sinks = pa.list_sinks()
    descriptions = pa.list_sink_descriptions()
    items = [("default", "Default (current default sink)")]
    for s in sinks:
        name = s["name"]
        items.append((name, descriptions.get(name, name)))
    return items


def is_internal_loopback(inp: dict) -> bool:
    props = inp.get("props", {})
    media = (props.get("media.name") or "").lower()
    nodeg = (props.get("node.group") or "").lower()
    noden = (props.get("node.name") or "").lower()
    return ("loopback" in media) or ("loopback" in nodeg) or (".loopback" in noden)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("AudioRouter")
        self.set_default_size(1180, 720)
        self.set_size_request(980, 620)

        self.cfg = load_config()
        self._apply_running = False
        self._apply_queued = False
        self._apply_refresh_requested = False

        self.stream_target_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.stream_move_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.stream_rule_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.mic_target_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.mic_move_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.mic_rule_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_content(root)

        header = Adw.HeaderBar()
        root.append(header)

        self._setup_header_menu(header)

        btn_donate = Gtk.Button(label="Donate ❤️")
        btn_donate.add_css_class("suggested-action")  # schöner GNOME-Look
        btn_donate.connect("clicked", open_donate)

        header.pack_start(btn_donate)

        # Autostart toggle (reboot-fest ohne GUI öffnen)
        self.autostart_check = Gtk.CheckButton(label="Beim Login automatisch starten (Background)")
        self.autostart_check.set_active(autostart_is_enabled())
        self.autostart_check.connect("toggled", self.on_autostart_toggled)
        root.append(self.autostart_check)

        quick_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        quick_actions.set_homogeneous(False)

        self.btn_policy_toggle = Gtk.Button(label="")
        self.btn_policy_toggle.add_css_class("suggested-action")
        self.btn_policy_toggle.connect("clicked", lambda *_: self.toggle_system_sound_policy())

        quick_actions.append(self.btn_policy_toggle)
        root.append(quick_actions)

        status_cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.status_card_pipewire = self._make_status_card("🔊", "PipeWire/Pulse")
        self.status_card_default_sink = self._make_status_card("🎯", "Default Sink")
        self.status_card_daemon = self._make_status_card("⚙️", "Daemon")
        self.status_card_streams = self._make_status_card("🎵", "Aktive Streams")

        for card in (
            self.status_card_pipewire,
            self.status_card_default_sink,
            self.status_card_daemon,
            self.status_card_streams,
        ):
            status_cards.append(card["frame"])

        root.append(status_cards)

        btn_refresh = Gtk.Button(label="Refresh")
        btn_refresh.connect("clicked", lambda *_: self.refresh_all())
        header.pack_end(btn_refresh)

        split = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        split.set_position(560)
        split.set_resize_start_child(True)
        split.set_shrink_start_child(False)
        split.set_shrink_end_child(False)
        root.append(split)

        # LEFT: buses
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_size_request(500, -1)
        split.set_start_child(left)

        left_title = Gtk.Label(label="Buses (virtual sinks)", xalign=0)
        left_title.add_css_class("title-3")
        left.append(left_title)

        bus_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                             margin_top=2, margin_bottom=2, margin_start=6, margin_end=6)
        hdr_name = Gtk.Label(label="Technical sink", xalign=0)
        hdr_name.set_width_chars(20)
        hdr_name.add_css_class("dim-label")
        hdr_label = Gtk.Label(label="Label", xalign=0)
        hdr_label.set_width_chars(10)
        hdr_label.add_css_class("dim-label")
        hdr_route = Gtk.Label(label="Route target", xalign=0)
        hdr_route.set_hexpand(True)
        hdr_route.set_margin_start(4)
        hdr_route.add_css_class("dim-label")
        hdr_action = Gtk.Label(label="Action", xalign=0)
        hdr_action.set_size_request(110, -1)
        hdr_action.add_css_class("dim-label")
        bus_header.append(hdr_name)
        bus_header.append(hdr_label)
        bus_header.append(hdr_route)
        bus_header.append(hdr_action)
        left.append(bus_header)

        self.bus_list = Gtk.ListBox()
        self.bus_list.set_selection_mode(Gtk.SelectionMode.NONE)
        buses_scroll = Gtk.ScrolledWindow()
        buses_scroll.set_vexpand(True)
        buses_scroll.set_hexpand(True)
        buses_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        buses_scroll.set_child(self.bus_list)
        left.append(buses_scroll)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_bus_label = Gtk.Entry(placeholder_text="Browser")
        self.entry_bus_label.connect("activate", lambda *_: self.add_bus())
        btn_add = Gtk.Button(label="Add Bus")
        btn_add.connect("clicked", lambda *_: self.add_bus())
        add_row.append(self.entry_bus_label)
        add_row.append(btn_add)
        left.append(add_row)

        # RIGHT: streams
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        split.set_end_child(right)

        right_title = Gtk.Label(label="Running application streams", xalign=0)
        right_title.add_css_class("title-3")
        right.append(right_title)

        streams_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                                 margin_top=2, margin_bottom=2, margin_start=0, margin_end=6)
        hdr_stream = Gtk.Label(label="Stream", xalign=0)
        hdr_stream.set_hexpand(True)
        hdr_stream.add_css_class("dim-label")
        hdr_target = Gtk.Label(label="Target bus", xalign=0)
        hdr_target.set_halign(Gtk.Align.START)
        hdr_target.add_css_class("dim-label")
        self.stream_target_group.add_widget(hdr_target)
        hdr_move = Gtk.Label(label="Move", xalign=0)
        hdr_move.set_halign(Gtk.Align.START)
        hdr_move.add_css_class("dim-label")
        self.stream_move_group.add_widget(hdr_move)
        hdr_rule = Gtk.Label(label="Rule", xalign=0)
        hdr_rule.set_halign(Gtk.Align.START)
        hdr_rule.add_css_class("dim-label")
        self.stream_rule_group.add_widget(hdr_rule)
        streams_header.append(hdr_stream)
        streams_header.append(hdr_target)
        streams_header.append(hdr_move)
        streams_header.append(hdr_rule)
        right.append(streams_header)

        self.stream_list = Gtk.ListBox()
        self.stream_list.set_selection_mode(Gtk.SelectionMode.NONE)
        streams_scroll = Gtk.ScrolledWindow()
        streams_scroll.set_vexpand(True)
        streams_scroll.set_hexpand(True)
        streams_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        streams_scroll.set_child(self.stream_list)
        right.append(streams_scroll)

        mic_title = Gtk.Label(label="Running input devices (OBS/Discord ready)", xalign=0)
        mic_title.add_css_class("title-3")
        right.append(mic_title)

        mic_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                             margin_top=2, margin_bottom=2, margin_start=0, margin_end=6)
        hdr_mic_stream = Gtk.Label(label="Input device", xalign=0)
        hdr_mic_stream.set_hexpand(True)
        hdr_mic_stream.add_css_class("dim-label")
        hdr_mic_target = Gtk.Label(label="Target bus", xalign=0)
        hdr_mic_target.set_halign(Gtk.Align.START)
        hdr_mic_target.add_css_class("dim-label")
        self.mic_target_group.add_widget(hdr_mic_target)
        hdr_mic_move = Gtk.Label(label="Move", xalign=0)
        hdr_mic_move.set_halign(Gtk.Align.START)
        hdr_mic_move.add_css_class("dim-label")
        self.mic_move_group.add_widget(hdr_mic_move)
        hdr_mic_rule = Gtk.Label(label="Rule", xalign=0)
        hdr_mic_rule.set_halign(Gtk.Align.START)
        hdr_mic_rule.add_css_class("dim-label")
        self.mic_rule_group.add_widget(hdr_mic_rule)
        mic_header.append(hdr_mic_stream)
        mic_header.append(hdr_mic_target)
        mic_header.append(hdr_mic_move)
        mic_header.append(hdr_mic_rule)
        right.append(mic_header)

        self.mic_stream_list = Gtk.ListBox()
        self.mic_stream_list.set_selection_mode(Gtk.SelectionMode.NONE)
        mic_scroll = Gtk.ScrolledWindow()
        mic_scroll.set_vexpand(True)
        mic_scroll.set_hexpand(True)
        mic_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        mic_scroll.set_min_content_height(180)
        mic_scroll.set_child(self.mic_stream_list)
        right.append(mic_scroll)

        apply_once()
        self.refresh_all()

    def _setup_header_menu(self, header: Adw.HeaderBar) -> None:
        actions = Gio.SimpleActionGroup()

        act_open_rules = Gio.SimpleAction.new("open_rules", None)
        act_open_rules.connect("activate", lambda *_: self.open_json_editor(RULES_PATH, "Routing Rules"))
        actions.add_action(act_open_rules)

        act_open_vsinks = Gio.SimpleAction.new("open_vsinks", None)
        act_open_vsinks.connect("activate", lambda *_: self.open_json_editor(VSINKS_PATH, "vSinks"))
        actions.add_action(act_open_vsinks)

        act_debug_snapshot = Gio.SimpleAction.new("debug_snapshot", None)
        act_debug_snapshot.connect("activate", lambda *_: self.open_debug_snapshot())
        actions.add_action(act_debug_snapshot)

        act_delete_lock = Gio.SimpleAction.new("delete_daemon_lock", None)
        act_delete_lock.connect("activate", lambda *_: self.delete_daemon_rules_file())
        actions.add_action(act_delete_lock)

        act_donate = Gio.SimpleAction.new("donate", None)
        act_donate.connect("activate", lambda *_: open_donate(None))
        actions.add_action(act_donate)

        self.insert_action_group("win", actions)

        cfg_menu = Gio.Menu()
        cfg_menu.append("Open Routing Rules", "win.open_rules")
        cfg_menu.append("Open vSinks", "win.open_vsinks")

        debug_menu = Gio.Menu()
        debug_menu.append("Audio Debug Snapshot", "win.debug_snapshot")
        debug_menu.append("Delete audiorouter-deamon.lock", "win.delete_daemon_lock")

        help_menu = Gio.Menu()
        help_menu.append("Donate", "win.donate")

        root_menu = Gio.Menu()
        root_menu.append_submenu("Configuration", cfg_menu)
        root_menu.append_submenu("DEBUG", debug_menu)
        root_menu.append_submenu("Help", help_menu)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_tooltip_text("Open menu")
        menu_btn.set_menu_model(root_menu)
        header.pack_end(menu_btn)

    def _make_status_card(self, icon: str, title: str) -> dict:
        frame = Gtk.Frame()
        frame.set_hexpand(True)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=10,
            margin_end=10,
        )

        title_lbl = Gtk.Label(label=f"{icon} {title}", xalign=0)
        title_lbl.add_css_class("dim-label")

        value_lbl = Gtk.Label(xalign=0)
        value_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        value_lbl.set_max_width_chars(36)

        box.append(title_lbl)
        box.append(value_lbl)
        frame.set_child(box)
        return {"frame": frame, "title": title_lbl, "value": value_lbl}

    def _set_status_card(self, card: dict, value: str) -> None:
        val = card["value"]
        val.set_text(value)
        val.set_tooltip_text(value)

    def _refresh_policy_toggle_button(self) -> None:
        installed = system_sound_policy_installed()
        if installed:
            self.btn_policy_toggle.set_label("System Sound Policy entfernen")
            self.btn_policy_toggle.remove_css_class("suggested-action")
            self.btn_policy_toggle.add_css_class("destructive-action")
            self.btn_policy_toggle.set_tooltip_text("Entfernt die Systemsound-Policy (route system sounds to vsink.system) und startet PipeWire/Pulse neu.")
        else:
            self.btn_policy_toggle.set_label("System Sound Policy installieren")
            self.btn_policy_toggle.remove_css_class("destructive-action")
            self.btn_policy_toggle.add_css_class("suggested-action")
            self.btn_policy_toggle.set_tooltip_text("Installiert die Systemsound-Policy (route system sounds to vsink.system) und startet PipeWire/Pulse neu.")

    def toggle_system_sound_policy(self) -> None:
        self._apply_system_policy_async(not system_sound_policy_installed())


    def open_json_editor(self, path: Path, title: str):
        # Ensure config files exist and are synced before opening editor.
        load_config()

        editor = Gtk.Window(title=f"{title} bearbeiten")
        editor.set_transient_for(self)
        editor.set_modal(True)
        editor.set_default_size(780, 520)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        editor.set_child(root)

        root.append(Gtk.Label(label=str(path), xalign=0))

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        root.append(sw)

        text_view = Gtk.TextView()
        text_view.set_monospace(True)
        buffer = text_view.get_buffer()

        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = "[]"
        buffer.set_text(content)

        sw.set_child(text_view)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_save = Gtk.Button(label="Save")
        btn_close = Gtk.Button(label="Close")
        actions.append(btn_save)
        actions.append(btn_close)
        root.append(actions)

        def on_save(_btn):
            start = buffer.get_start_iter()
            end = buffer.get_end_iter()
            new_text = buffer.get_text(start, end, True)
            try:
                path.write_text(new_text, encoding="utf-8")
                # reload + sync legacy config.json and in-memory cfg
                self.cfg = load_config()
                self.refresh_all()
            except Exception:
                pass

        btn_save.connect("clicked", on_save)
        btn_close.connect("clicked", lambda *_: editor.close())

        editor.present()

    def open_debug_snapshot(self):
        editor = Gtk.Window(title="Audio debug snapshot")
        editor.set_transient_for(self)
        editor.set_modal(True)
        editor.set_default_size(840, 560)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        editor.set_child(root)

        help_lbl = Gtk.Label(
            label="Share this snapshot when noise appears during routing switch."
                  " It contains pactl state (sinks/sources/modules/inputs).",
            xalign=0
        )
        help_lbl.set_wrap(True)
        root.append(help_lbl)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        root.append(sw)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        buffer = text_view.get_buffer()
        buffer.set_text(pa.collect_debug_snapshot())
        sw.set_child(text_view)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_copy = Gtk.Button(label="Copy")
        btn_save = Gtk.Button(label="Save to file")
        btn_close = Gtk.Button(label="Close")
        actions.append(btn_copy)
        actions.append(btn_save)
        actions.append(btn_close)
        root.append(actions)

        def _current_text() -> str:
            start = buffer.get_start_iter()
            end = buffer.get_end_iter()
            return buffer.get_text(start, end, True)

        def on_copy(_btn):
            clip = self.get_clipboard()
            clip.set(_current_text())

        def on_save(_btn):
            debug_dir = Path.home() / ".config" / "audiorouter"
            debug_dir.mkdir(parents=True, exist_ok=True)
            out = debug_dir / "debug-snapshot.txt"
            out.write_text(_current_text(), encoding="utf-8")

        btn_copy.connect("clicked", on_copy)
        btn_save.connect("clicked", on_save)
        btn_close.connect("clicked", lambda *_: editor.close())

        editor.present()

    def _show_message(self, title: str, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            buttons=Gtk.ButtonsType.OK,
            text=title,
            secondary_text=message,
        )
        dialog.connect("response", lambda d, _r: d.close())
        dialog.present()

    def _reload_audio_stack_and_reapply(self) -> None:
        restart_pipewire_pulse()

        # PipeWire restart drops runtime virtual sinks/modules. Re-apply routing
        # once the server is reachable again so vsinks are recreated.
        for _ in range(30):
            if pa.try_pactl("info").strip():
                try:
                    apply_once()
                except Exception:
                    pass
                return
            time.sleep(0.2)

    def _ensure_system_bus_exists(self) -> None:
        cfg = load_config()
        cfg.setdefault("buses", [])
        if any((b.get("name") == "vsink.system") for b in cfg.get("buses", [])):
            return
        cfg["buses"].append({"name": "vsink.system", "label": "System", "route_to": "default"})
        save_config(cfg)
        apply_once()

    def _apply_system_policy_async(self, install: bool) -> None:
        def _worker():
            try:
                if install:
                    self._ensure_system_bus_exists()
                    path = install_system_sound_policy("vsink.system")
                    self._reload_audio_stack_and_reapply()
                    msg = f"Policy installed:\n{path}\n\nPipeWire-Pulse was restarted."
                    title = "System sound policy installed"
                else:
                    path = remove_system_sound_policy()
                    self._reload_audio_stack_and_reapply()
                    msg = f"Policy removed:\n{path}\n\nPipeWire-Pulse was restarted."
                    title = "System sound policy removed"
            except Exception as exc:
                title = "System sound policy error"
                msg = str(exc)

            GLib.idle_add(self.refresh_all)
            GLib.idle_add(self._show_message, title, msg)

        threading.Thread(target=_worker, daemon=True).start()

    def install_system_sound_policy(self) -> None:
        self._apply_system_policy_async(True)

    def remove_system_sound_policy(self) -> None:
        self._apply_system_policy_async(False)

    def delete_daemon_rules_file(self) -> None:
        def _worker():
            try:
                cache_dir = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
                requested = cache_dir / "audiorouter-deamon.lock"
                legacy = cache_dir / "audiorouter-daemon.lock"

                deleted: list[Path] = []
                for path in (requested, legacy):
                    if path.exists():
                        path.unlink()
                        deleted.append(path)

                if deleted:
                    title = "audiorouter-deamon.lock deleted"
                    msg = "Deleted file(s):\n" + "\n".join(str(p) for p in deleted)
                else:
                    title = "No lock file found"
                    msg = f"Neither of these files exists:\n{requested}\n{legacy}"
            except Exception as exc:
                title = "Delete audiorouter-deamon.lock error"
                msg = str(exc)

            GLib.idle_add(self.refresh_all)
            GLib.idle_add(self._show_message, title, msg)

        threading.Thread(target=_worker, daemon=True).start()

    def refresh_all(self):
        self.cfg = load_config()
        self.refresh_buses()
        stream_count = self.refresh_streams()
        mic_count = self.refresh_mic_streams()
        self.refresh_status(stream_count + mic_count)
        self._refresh_policy_toggle_button()

    def on_autostart_toggled(self, btn: Gtk.CheckButton):
        state = btn.get_active()
        if state:
            autostart_enable()
        else:
            autostart_disable()


    def _is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _daemon_running(self) -> bool:
        lock_file = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "audiorouter-daemon.lock"
        if not lock_file.exists():
            return False
        try:
            pid = int(lock_file.read_text(encoding="utf-8").strip())
        except Exception:
            return False
        return self._is_pid_alive(pid)

    def refresh_status(self, stream_count: int):
        info = pa.try_pactl("info")
        pipewire_ok = bool(info.strip())

        if pipewire_ok:
            default_sink = pa.get_default_sink() or "-"
            sink_count = len(pa.list_sinks())
            sink_desc = pa.list_sink_descriptions().get(default_sink, default_sink)
            self._set_status_card(self.status_card_pipewire, f"✅ bereit ({sink_count} Sinks)")
            self._set_status_card(self.status_card_default_sink, sink_desc)
        else:
            self._set_status_card(self.status_card_pipewire, "❌ nicht erreichbar")
            self._set_status_card(self.status_card_default_sink, "-")

        self._set_status_card(self.status_card_daemon, "✅ läuft" if self._daemon_running() else "⚠️ gestoppt")
        self._set_status_card(self.status_card_streams, str(stream_count))

    def refresh_buses(self):
        for child in list(self.bus_list):
            self.bus_list.remove(child)

        sink_items = friendly_sink_list()
        sink_labels = [t for _, t in sink_items]

        buses = self.cfg.get("buses", [])
        if not buses:
            # placeholder
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)
            box.append(Gtk.Label(label="Noch keine Buses. Links unten Add Bus benutzen.", xalign=0))
            self.bus_list.append(row)
            return

        for b in buses:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)

            name_lbl = Gtk.Label(label=b["name"], xalign=0)
            name_lbl.set_width_chars(20)
            name_lbl.set_max_width_chars(20)
            name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(name_lbl)

            label_lbl = Gtk.Label(label=b.get("label", ""), xalign=0)
            label_lbl.set_width_chars(10)
            label_lbl.set_max_width_chars(12)
            label_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(label_lbl)

            dd = Gtk.DropDown.new_from_strings(sink_labels)
            dd.set_hexpand(True)
            route_to = b.get("route_to", "default")
            idx = 0
            for i, (val, _) in enumerate(sink_items):
                if val == route_to:
                    idx = i
                    break
            dd.set_selected(idx)

            def on_change(dropdown, _pspec, bus_name=b["name"], items=sink_items):
                sel = dropdown.get_selected()
                value = items[sel][0]
                self.set_route(bus_name, value)

            dd.connect("notify::selected", on_change)
            box.append(dd)

            btn_del = Gtk.Button(label="Delete")
            btn_del.set_size_request(110, -1)
            btn_del.connect("clicked", lambda *_ , bus=b["name"]: self.delete_bus(bus))
            box.append(btn_del)

            self.bus_list.append(row)

    def _stream_match_obj(self, app: str, binary: str, app_id: str) -> dict:
        # gleiche Priorität wie beim Add Rule
        if binary:
            return {"binary": binary}
        if app_id:
            return {"app_id": app_id}
        if app and app != "Unknown":
            return {"app": app}
        return {}

    def _find_rule_index(self, rules: list, match: dict) -> int:
        # exakter match-Vergleich: {"binary":"vivaldi"} etc.
        for idx, r in enumerate(rules):
            if r.get("match") == match:
                return idx
        return -1
 
 
    def _find_input_rule_index(self, rules: list, source_name: str) -> int:
        for idx, r in enumerate(rules):
            if str(r.get("source", "")).strip() == source_name:
                return idx
        return -1

    def refresh_mic_streams(self):
        for child in list(self.mic_stream_list):
            self.mic_stream_list.remove(child)

        sources = [s for s in pa.list_sources() if not s.get("name", "").endswith(".monitor")]

        if not sources:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)
            box.append(Gtk.Label(label="Keine Eingabegeräte gefunden.", xalign=0))
            self.mic_stream_list.append(row)
            return 0

        buses = [b["name"] for b in self.cfg.get("buses", [])]
        input_routes = self.cfg.get("input_routes", [])
        source_desc = pa.list_source_descriptions()

        for src in sources:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)

            source_name = str(src.get("name", ""))
            sid = str(src.get("id", ""))

            friendly = source_desc.get(source_name, source_name)
            label = Gtk.Label(label=f"#{sid}  {friendly}\n{source_name}", xalign=0)
            label.set_hexpand(True)
            label.set_wrap(True)
            label.set_tooltip_text(source_name)
            box.append(label)

            if buses:
                dd = Gtk.DropDown.new_from_strings(buses)
                dd.set_size_request(170, -1)
                self.mic_target_group.add_widget(dd)

                rule_idx = self._find_input_rule_index(input_routes, source_name)
                has_rule = rule_idx >= 0
                if has_rule:
                    target_bus = input_routes[rule_idx].get("target_bus")
                    if target_bus in buses:
                        dd.set_selected(buses.index(target_bus))
                    else:
                        dd.set_selected(0)
                else:
                    dd.set_selected(0)

                def on_move(_btn, source_name=source_name, dropdown=dd):
                    tgt_bus = buses[dropdown.get_selected()]
                    try:
                        # transient move only: do not create/update persistent Add Rule entries
                        pa.cleanup_wrong_loopbacks_for_source(source_name, tgt_bus)
                        if not pa.loopback_exists(source_name, tgt_bus):
                            pa.load_loopback(source_name, tgt_bus, latency_msec=30)
                    except Exception:
                        pass
                    self.refresh_all()

                btn_move = Gtk.Button(label="Route to Bus")
                btn_move.set_size_request(110, -1)
                self.mic_move_group.add_widget(btn_move)
                btn_move.connect("clicked", on_move)
                box.append(dd)
                box.append(btn_move)

                btn_rule = Gtk.Button(label=("Delete Rule" if has_rule else "Add Rule"))
                btn_rule.set_size_request(110, -1)
                self.mic_rule_group.add_widget(btn_rule)
                if has_rule:
                    btn_rule.add_css_class("suggested-action")

                def on_rule_toggle(_btn, dropdown=dd, source_name=source_name, has_rule=has_rule):
                    cfg = load_config()
                    cfg.setdefault("input_routes", [])

                    if has_rule:
                        cfg["input_routes"] = [r for r in cfg["input_routes"] if str(r.get("source", "")).strip() != source_name]
                        save_config(cfg)
                        apply_once()
                        self.refresh_all()
                        return

                    target = buses[dropdown.get_selected()]
                    cfg["input_routes"] = [r for r in cfg["input_routes"] if str(r.get("source", "")).strip() != source_name]
                    cfg["input_routes"].append({"source": source_name, "target_bus": target})
                    save_config(cfg)
                    apply_once()
                    self.refresh_all()

                btn_rule.connect("clicked", on_rule_toggle)
                box.append(btn_rule)

            self.mic_stream_list.append(row)

        return len(sources)

    def refresh_streams(self):
        for child in list(self.stream_list):
            self.stream_list.remove(child)

        inputs = pa.list_sink_inputs()

        # Filter loopbacks (routing internals)
        inputs = [i for i in inputs if (not i.get("props", {})) or not is_internal_loopback(i)]

        if not inputs:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)
            box.append(Gtk.Label(
                label="Keine aktiven App-Audio-Streams. Starte Audio (YouTube/Spotify) und drücke Refresh.",
                xalign=0
            ))
            self.stream_list.append(row)
            return 0

        buses = [b["name"] for b in self.cfg.get("buses", [])]
        rules = self.cfg.get("rules", [])

        # Map sink_id -> sink_name
        sink_id_to_name = {s["id"]: s["name"] for s in pa.list_sinks()}


        for inp in inputs:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            row.set_child(box)

            props = inp.get("props", {})
            app = props.get("application.name") or props.get("pipewire.access.portal.app_id") or "Unknown"
            app_id = props.get("pipewire.access.portal.app_id") or ""
            binary = props.get("application.process.binary") or ""
            media = props.get("media.name") or ""

            sid = str(inp.get("id"))

            label = Gtk.Label(label=f"#{sid}  {app}  ({binary or '?'}) — {media}", xalign=0)
            label.set_hexpand(True)
            box.append(label)

            if buses:
                dd = Gtk.DropDown.new_from_strings(buses)
                dd.set_size_request(170, -1)
                self.stream_target_group.add_widget(dd)

                # Prefer: actual current sink of this stream (sink_id)
                cur_sink_id = str(inp.get("sink_id", ""))
                cur_sink_name = sink_id_to_name.get(cur_sink_id, "")

                # If the stream is currently on one of our buses, select that bus in dropdown
                if cur_sink_name in buses:
                    dd.set_selected(buses.index(cur_sink_name))
                else:
                    # otherwise default to first bus (or keep 0)
                    dd.set_selected(0)


                def on_move(_btn, sink_input_id=sid, dropdown=dd):
                    tgt = buses[dropdown.get_selected()]
                    try:
                        pa.move_sink_input(sink_input_id, tgt)
                    except Exception:
                        pass
                    self.refresh_all()

                btn_move = Gtk.Button(label="Move to Bus")
                btn_move.set_size_request(110, -1)
                self.stream_move_group.add_widget(btn_move)
                btn_move.connect("clicked", on_move)
                box.append(dd)
                box.append(btn_move)

                # --- Rule UI (Add / Delete toggle) ---
                match = self._stream_match_obj(app, binary, app_id)
                rule_idx = self._find_rule_index(rules, match) if match else -1
                has_rule = rule_idx >= 0

                # If rule exists: preselect its target bus in the dropdown
                if has_rule:
                    target_bus = rules[rule_idx].get("target_bus")
                    if target_bus in buses:
                        dd.set_selected(buses.index(target_bus))

                btn_rule = Gtk.Button(label=("Delete Rule" if has_rule else "Add Rule"))
                btn_rule.set_size_request(110, -1)
                self.stream_rule_group.add_widget(btn_rule)
                if has_rule:
                    btn_rule.add_css_class("suggested-action")  # visually highlight

                def on_rule_toggle(_btn, dropdown=dd, match=match, has_rule=has_rule):
                    if not match:
                        return

                    cfg = load_config()
                    cfg.setdefault("rules", [])

                    if has_rule:
                        # delete rule
                        cfg["rules"] = [r for r in cfg["rules"] if r.get("match") != match]
                        save_config(cfg)
                        apply_once()
                        self.refresh_all()
                        return

                    # add rule
                    target = buses[dropdown.get_selected()]
                    cfg["rules"].append({"match": match, "target_bus": target})
                    save_config(cfg)
                    apply_once()
                    self.refresh_all()

                btn_rule.connect("clicked", on_rule_toggle)
                box.append(btn_rule)


            self.stream_list.append(row)

        return len(inputs)

    def add_bus(self):
        label = self.entry_bus_label.get_text().strip()
        if not label:
            return

        cfg = load_config()
        cfg.setdefault("buses", [])

        existing = {b.get("name") for b in cfg["buses"] if b.get("name")}
        name = make_bus_name(label, existing)

        cfg["buses"].append({"name": name, "label": label, "route_to": "default"})
        save_config(cfg)

        self.entry_bus_label.set_text("")
        apply_once()
        self.refresh_all()
        self.entry_bus_label.grab_focus()



    def delete_bus(self, bus_name: str):
        cfg = load_config()
        cfg["buses"] = [b for b in cfg.get("buses", []) if b["name"] != bus_name]
        cfg["rules"] = [r for r in cfg.get("rules", []) if r.get("target_bus") != bus_name]
        save_config(cfg)
        apply_once()
        self.refresh_all()

    def _apply_once_async(self, refresh_ui: bool = True):
        # Keep route changes responsive: run potentially slow apply_once() off the GTK main thread.
        self._apply_refresh_requested = self._apply_refresh_requested or refresh_ui
        if self._apply_running:
            self._apply_queued = True
            return

        self._apply_running = True

        def worker():
            try:
                apply_once()
            finally:
                def on_done():
                    self._apply_running = False
                    do_refresh = self._apply_refresh_requested
                    self._apply_refresh_requested = False
                    run_again = self._apply_queued
                    self._apply_queued = False

                    if do_refresh:
                        self.refresh_all()
                    if run_again:
                        self._apply_once_async(refresh_ui=True)
                    return False

                GLib.idle_add(on_done)

        threading.Thread(target=worker, daemon=True).start()

    def set_route(self, bus_name: str, route_to: str):
        cfg = load_config()
        for b in cfg.get("buses", []):
            if b["name"] == bus_name:
                b["route_to"] = route_to
        save_config(cfg)
        self._apply_once_async(refresh_ui=False)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=0)

    def do_activate(self):
        win = MainWindow(self)
        win.present()


def main():
    app = App()
    app.run(None)


if __name__ == "__main__":
    main()
