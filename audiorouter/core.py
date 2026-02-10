from __future__ import annotations

"""Core logic for audiorouter.

This module contains the *idempotent* reconciliation step.
It is shared by both the GUI and the background daemon.

The reconciliation does four things:
1) Unloads modules for buses that were removed from the config.
2) Ensures every configured bus has a module-null-sink.
3) Ensures every bus is routed to its configured target (default or a real sink)
   by creating a module-loopback from "<bus>.monitor" to the target.
4) Applies stream rules by moving matching sink-inputs to the bus sink.
"""

from .config import load_config, load_state, save_state
from . import pactl as pa


def apply_once() -> None:
    cfg = load_config()
    st = load_state()

    st.setdefault("bus_modules", {})     # bus_name -> module_id (null-sink)
    st.setdefault("route_modules", {})   # bus_name -> module_id (loopback)
    st.setdefault("route_target", {})    # bus_name -> last target sink

    buses = cfg.get("buses", [])
    rules = cfg.get("rules", [])

    # ---- Cleanup removed buses ----
    current_bus_names = {b["name"] for b in buses}

    # Unload loopbacks for buses that no longer exist
    for bus_name in list(st.get("route_modules", {}).keys()):
        if bus_name not in current_bus_names:
            pa.unload_module(st["route_modules"][bus_name])
            del st["route_modules"][bus_name]
            st.get("route_target", {}).pop(bus_name, None)

    # Unload null-sink modules for buses that no longer exist
    for bus_name in list(st.get("bus_modules", {}).keys()):
        if bus_name not in current_bus_names:
            pa.unload_module(st["bus_modules"][bus_name])
            del st["bus_modules"][bus_name]

    # 1) Ensure null sinks exist for every bus
    for b in buses:
        name = b["name"]
        label = b.get("label", name)

        if not pa.sink_exists(name):
            mid = pa.load_null_sink(name, label)
            st["bus_modules"][name] = mid

    # 2) Routing: bus.monitor -> route_to target
    for b in buses:
        name = b["name"]
        route_to = b.get("route_to", "default")

        target = pa.get_default_sink() if route_to == "default" else route_to
        if not target or target == name:
            continue

        monitor = f"{name}.monitor"

        # PipeWire erstellt monitor kurz nach sink
        if not pa.source_exists(monitor):
            continue

        # Immer zuerst alte Loopbacks dieser Route entfernen (gegen Duplikate)
        pa.cleanup_loopbacks_for_route(monitor, target)

        last = st["route_target"].get(name)
        old_mod = st["route_modules"].get(name)

        # Wenn target gleich geblieben ist und wir noch ein Modul haben: passt
        if last == target and old_mod:
            continue

        # Wenn target gewechselt hat: altes Modul entfernen
        if old_mod:
            pa.unload_module(old_mod)

        new_mod = pa.load_loopback(monitor, target, latency_msec=30)
        st["route_modules"][name] = new_mod
        st["route_target"][name] = target

        
    # 3) Apply rules: move matching app streams to bus sink
    inputs = pa.list_sink_inputs()
    for inp in inputs:
        props = inp.get("props", {})
        app = (props.get("application.name") or "").lower()
        bin_ = (props.get("application.process.binary") or "").lower()
        aid = (props.get("pipewire.access.portal.app_id") or "").lower()

        for r in rules:
            match = r.get("match", {})
            tgt = r.get("target_bus")

            if not tgt or not pa.sink_exists(tgt):
                continue

            ok = True
            if "binary" in match and match["binary"].lower() not in bin_:
                ok = False
            if "app" in match and match["app"].lower() not in app:
                ok = False
            if "app_id" in match and match["app_id"].lower() not in aid:
                ok = False

            if ok:
                try:
                    pa.move_sink_input(str(inp["id"]), tgt)
                except Exception:
                    pass

    save_state(st)
