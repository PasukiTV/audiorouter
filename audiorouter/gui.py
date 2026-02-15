#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Pango

from .autostart import is_enabled as autostart_is_enabled, enable as autostart_enable, disable as autostart_disable


from .config import RULES_PATH, VSINKS_PATH, load_config, save_config
from . import pactl as pa
# Apply changes immediately (no "Apply" button)
from .core import apply_once

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
    items = [("default", "Default (current default sink)")]
    for s in sinks:
        items.append((s["name"], s["name"]))
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
        self.set_title("audiorouter")
        self.set_default_size(1180, 720)
        self.set_size_request(980, 620)

        self.cfg = load_config()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_content(root)

        header = Adw.HeaderBar()
        root.append(header)

        btn_donate = Gtk.Button(label="Donate ❤️")
        btn_donate.add_css_class("suggested-action")  # schöner GNOME-Look
        btn_donate.connect("clicked", open_donate)

        header.pack_start(btn_donate)

        # Autostart toggle (reboot-fest ohne GUI öffnen)
        self.autostart_check = Gtk.CheckButton(label="Beim Login automatisch starten (Background)")
        self.autostart_check.set_active(autostart_is_enabled())
        self.autostart_check.connect("toggled", self.on_autostart_toggled)
        root.append(self.autostart_check)

        file_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_open_rules = Gtk.Button(label="Open Routing Rules")
        btn_open_rules.connect("clicked", lambda *_: self.open_json_editor(RULES_PATH, "Routing Rules"))
        btn_open_vsinks = Gtk.Button(label="Open vSinks")
        btn_open_vsinks.connect("clicked", lambda *_: self.open_json_editor(VSINKS_PATH, "vSinks"))
        file_buttons.append(btn_open_rules)
        file_buttons.append(btn_open_vsinks)
        root.append(file_buttons)

        # Lightweight status row (updates only on refresh)
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.status_pipewire = Gtk.Label(xalign=0)
        self.status_default_sink = Gtk.Label(xalign=0)
        self.status_daemon = Gtk.Label(xalign=0)
        self.status_streams = Gtk.Label(xalign=0)
        for lbl in (self.status_pipewire, self.status_default_sink, self.status_daemon, self.status_streams):
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(42)
        status_row.append(self.status_pipewire)
        status_row.append(self.status_default_sink)
        status_row.append(self.status_daemon)
        status_row.append(self.status_streams)
        root.append(status_row)

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
        hdr_route.add_css_class("dim-label")
        hdr_action = Gtk.Label(label="Action", xalign=0)
        hdr_action.add_css_class("dim-label")
        bus_header.append(hdr_name)
        bus_header.append(hdr_label)
        bus_header.append(hdr_route)
        bus_header.append(hdr_action)
        left.append(bus_header)

        self.bus_list = Gtk.ListBox()
        self.bus_list.set_selection_mode(Gtk.SelectionMode.NONE)
        left.append(self.bus_list)

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
                                 margin_top=2, margin_bottom=2, margin_start=6, margin_end=6)
        hdr_stream = Gtk.Label(label="Stream", xalign=0)
        hdr_stream.set_hexpand(True)
        hdr_stream.add_css_class("dim-label")
        hdr_target = Gtk.Label(label="Target bus", xalign=0)
        hdr_target.add_css_class("dim-label")
        hdr_move = Gtk.Label(label="Move", xalign=0)
        hdr_move.add_css_class("dim-label")
        hdr_rule = Gtk.Label(label="Rule", xalign=0)
        hdr_rule.add_css_class("dim-label")
        streams_header.append(hdr_stream)
        streams_header.append(hdr_target)
        streams_header.append(hdr_move)
        streams_header.append(hdr_rule)
        right.append(streams_header)

        self.stream_list = Gtk.ListBox()
        self.stream_list.set_selection_mode(Gtk.SelectionMode.NONE)
        right.append(self.stream_list)


        apply_once()
        self.refresh_all()


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

    def refresh_all(self):
        self.cfg = load_config()
        self.refresh_buses()
        stream_count = self.refresh_streams()
        self.refresh_status(stream_count)

    def on_autostart_toggled(self, btn: Gtk.CheckButton):
        state = btn.get_active()
        print("AUTOSTART TOGGLED:", state)

        if state:
            print("-> enable()")
            autostart_enable()
        else:
            print("-> disable()")
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
            self.status_pipewire.set_text(f"PipeWire/Pulse: ✅ bereit ({sink_count} Sinks)")
            self.status_default_sink.set_text(f"Default Sink: {sink_desc}")
        else:
            self.status_pipewire.set_text("PipeWire/Pulse: ❌ nicht erreichbar")
            self.status_default_sink.set_text("Default Sink: -")

        self.status_daemon.set_text(f"Daemon: {'✅ läuft' if self._daemon_running() else '⚠️ gestoppt'}")
        self.status_streams.set_text(f"Aktive Streams: {stream_count}")

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
 
 
    def refresh_streams(self):
        for child in list(self.stream_list):
            self.stream_list.remove(child)

        inputs = pa.list_sink_inputs()

        # Filter loopbacks (routing internals)
        filtered = []
        for i in inputs:
            props = i.get("props", {})
            if not props or not is_internal_loopback(i):
                filtered.append(i)
        inputs = filtered

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

    def set_route(self, bus_name: str, route_to: str):
        cfg = load_config()
        for b in cfg.get("buses", []):
            if b["name"] == bus_name:
                b["route_to"] = route_to
        save_config(cfg)
        apply_once()


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
