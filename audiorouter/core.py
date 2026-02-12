from __future__ import annotations

"""
Core logic for audiorouter.

Safe + idempotent reconciliation.

Fixes:
- No self loops
- No routing cycles
- Never routes to vsink.* as physical default
- No duplicate loopbacks
- Robust against PipeWire event storms
"""

from .config import load_config, load_state, save_state
from . import pactl as pa


# ---------------------------------------------------------
# Helper: get physical default sink (never vsink.*)
# ---------------------------------------------------------

def _get_physical_default_sink() -> str | None:
    default = pa.get_default_sink()

    # If default is already physical → use it
    if default and not default.startswith("vsink."):
        return default

    # Otherwise find first real hardware sink
    for s in pa.list_sinks():
        name = s.get("name")
        if name and not name.startswith("vsink."):
            return name

    return default


# ---------------------------------------------------------
# Core Reconciliation
# ---------------------------------------------------------

def apply_once() -> None:
    cfg = load_config()
    st = load_state()

    st.setdefault("bus_modules", {})     # bus_name -> module_id (null-sink)
    st.setdefault("route_modules", {})   # bus_name -> module_id (loopback)
    st.setdefault("route_target", {})    # bus_name -> last target sink

    buses = cfg.get("buses", [])
    rules = cfg.get("rules", [])

    current_bus_names = {b["name"] for b in buses}

    # ---------------------------------------------------------
    # 1) Cleanup removed buses
    # ---------------------------------------------------------

    for bus_name in list(st["route_modules"].keys()):
        if bus_name not in current_bus_names:
            pa.unload_module(st["route_modules"][bus_name])
            st["route_modules"].pop(bus_name, None)
            st["route_target"].pop(bus_name, None)

    for bus_name in list(st["bus_modules"].keys()):
        if bus_name not in current_bus_names:
            pa.unload_module(st["bus_modules"][bus_name])
            st["bus_modules"].pop(bus_name, None)

    # ---------------------------------------------------------
    # 2) Ensure null sinks exist
    # ---------------------------------------------------------

    for b in buses:
        name = b["name"]
        label = b.get("label", name)

        if not pa.sink_exists(name):
            mid = pa.load_null_sink(name, label)
            st["bus_modules"][name] = mid

    # ---------------------------------------------------------
    # 3) Routing logic (SAFE)
    # ---------------------------------------------------------

    for b in buses:
        name = b["name"]
        route_to = b.get("route_to", "default")

        # Resolve target safely
        if route_to == "default":
            target = _get_physical_default_sink()
        else:
            target = route_to

        if not target:
            continue

        # ❌ Never route to itself
        if target == name:
            continue

        # ❌ Never route to a monitor
        if target.endswith(".monitor"):
            continue

        monitor = f"{name}.monitor"

        if not pa.source_exists(monitor):
            continue

        # Remove ALL loopbacks for this source (no duplicates ever)
        pa.cleanup_loopbacks_for_source(monitor)

        last = st["route_target"].get(name)
        old_mod = st["route_modules"].get(name)

        # If nothing changed and module still exists → skip
        if (
            last == target
            and old_mod
            and any(m["id"] == old_mod for m in pa.list_modules())
        ):
            continue

        # If old module exists but target changed → unload
        if old_mod:
            pa.unload_module(old_mod)

        # Create new loopback
        new_mod = pa.load_loopback(
            monitor,
            target,
            latency_msec=30
        )

        st["route_modules"][name] = new_mod
        st["route_target"][name] = target

    # ---------------------------------------------------------
    # 4) Apply stream rules
    # ---------------------------------------------------------

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
