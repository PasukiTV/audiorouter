from __future__ import annotations

import time

VIRTUAL_SWITCH_MUTE_SEC = 0.12
PHYSICAL_SWITCH_MUTE_SEC = 0.05
SYSTEM_STREAM_MOVE_MUTE_SEC = 0.0

"""
Core logic for audiorouter.

Safe + idempotent reconciliation.

Fixes:
- Never routes to vsink.* as physical default
- No self loops
- No duplicate loopbacks (no create/delete loop)
- Only removes wrong loopbacks (same source, different sink)
- Route handover mutes bus sink + loopback sink-input to reduce switch artifacts
"""

from .config import load_config, load_state, save_state
from .trace import trace
from . import pactl as pa


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



def _is_system_stream(props: dict) -> bool:
    """
    Heuristic classification for short system sounds/notifications so they can
    be routed to the system bus immediately.
    """
    role = (props.get("media.role") or "").lower()
    if role in {"event", "notification"}:
        return True

    app = (props.get("application.name") or "").lower()
    binary = (props.get("application.process.binary") or "").lower()
    app_id = (props.get("pipewire.access.portal.app_id") or "").lower()
    media_name = (props.get("media.name") or "").lower()

    known_apps = {
        "gnome-shell",
        "plasmashell",
        "kded5",
        "kded6",
        "xfce4-notifyd",
        "notification-daemon",
        "mako",
    }
    known_bins = {
        "gnome-shell",
        "plasmashell",
        "xfce4-notifyd",
        "notification-daemon",
        "mako",
        "canberra-gtk-play",
    }

    if app in known_apps or binary in known_bins:
        return True

    if app_id.startswith("org.freedesktop.impl.portal") and "portal" in media_name:
        return True

    return any(token in media_name for token in {"system sound", "system sounds", "systemklänge", "benachrichtigung", "notification", "event"})


def _move_input_quietly(sink_input_id: str, target_sink: str, mute_sec: float = 0.0) -> None:
    trace(f"move_input_quietly start sink_input={sink_input_id} target={target_sink} mute_sec={mute_sec}")

    # For very short system streams, extra mute/unmute pactl calls are often
    # slower than the stream lifetime itself. Use direct move when mute window
    # is disabled.
    if mute_sec <= 0:
        pa.move_sink_input(sink_input_id, target_sink)
        trace(f"move_input_quietly done sink_input={sink_input_id} target={target_sink}")
        return

    pa.set_sink_input_mute(sink_input_id, True)
    try:
        pa.move_sink_input(sink_input_id, target_sink)
        time.sleep(mute_sec)
    finally:
        pa.set_sink_input_mute(sink_input_id, False)
        trace(f"move_input_quietly done sink_input={sink_input_id} target={target_sink}")




def route_sink_input_now(sink_input_id: str) -> bool:
    """
    Fast path for freshly created sink-inputs: try rule/system routing directly
    by id, before a full apply_once() reconciliation.
    """
    sid = str(sink_input_id).strip()
    if not sid:
        return False

    cfg = load_config()
    rules = cfg.get("rules", [])

    target_inp = None
    for inp in pa.list_sink_inputs():
        if str(inp.get("id", "")).strip() == sid:
            target_inp = inp
            break

    if not target_inp:
        return False

    props = target_inp.get("props", {})
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
                pa.move_sink_input(sid, tgt)
                trace(f"route_sink_input_now moved sink_input={sid} target={tgt} reason=rule")
                return True
            except Exception as exc:
                trace(f"route_sink_input_now_error sink_input={sid} target={tgt} reason=rule err={exc}")
                return False

    system_bus = "vsink.system"
    if pa.sink_exists(system_bus) and _is_system_stream(props):
        sink_id = str(target_inp.get("sink_id", "")).strip()
        sink_name_by_id = {str(s.get("id", "")).strip(): str(s.get("name", "")) for s in pa.list_sinks()}
        if sink_name_by_id.get(sink_id, "") == system_bus:
            return True
        try:
            _move_input_quietly(sid, system_bus, mute_sec=SYSTEM_STREAM_MOVE_MUTE_SEC)
            trace(f"route_sink_input_now moved sink_input={sid} target={system_bus} reason=system")
            return True
        except Exception as exc:
            trace(f"route_sink_input_now_error sink_input={sid} target={system_bus} reason=system err={exc}")
            return False

    return False

def apply_once() -> None:
    cfg = load_config()
    st = load_state()

    st.setdefault("bus_modules", {})     # bus_name -> module_id (null-sink)
    st.setdefault("route_modules", {})   # bus_name -> module_id (loopback) (optional)
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

        # Keep role metadata on existing system sink too (important after upgrades).
        if name == "vsink.system":
            pa.tag_system_sink(name)

    # ---------------------------------------------------------
    # 3) Routing logic (NO LOOPBACK CHURN)
    # ---------------------------------------------------------
    for b in buses:
        name = b["name"]
        route_to = b.get("route_to", "default")

        # Resolve target safely
        target = _get_physical_default_sink() if route_to == "default" else route_to
        if not target:
            continue

        # Never route to itself / monitors
        if target == name or target.endswith(".monitor"):
            continue

        monitor = f"{name}.monitor"

        # PipeWire may create monitor shortly after sink
        if not pa.source_exists(monitor):
            continue

        # ✅ If correct loopback already exists: keep it, do nothing
        if pa.loopback_exists(monitor, target):
            st["route_target"][name] = target
            # Optional: remove wrong ones (same source -> other sink)
            pa.cleanup_wrong_loopbacks_for_source(monitor, target)
            continue

        prev_target = st["route_target"].get(name, "")
        prev_mod = str(st["route_modules"].get(name, "") or "")
        involves_virtual = target.startswith("vsink.") or str(prev_target).startswith("vsink.")

        # Mute the bus sink itself (not only the monitor source) to avoid audible pops
        # while loopback modules are recreated. Also mute sink-inputs owned by the old
        # loopback module when we can resolve them.
        prev_inputs = pa.sink_inputs_for_owner_module(prev_mod)
        pa.set_sink_mute(name, True)
        pa.set_source_mute(monitor, True)
        for sid in prev_inputs:
            pa.set_sink_input_mute(sid, True)

        new_mod = ""
        try:
            if involves_virtual:
                # For virtual-bus handover use break-before-make while muted to avoid
                # comb/feedback-like artifacts when jumping between vsinks.
                pa.cleanup_wrong_loopbacks_for_source(monitor, target)
                time.sleep(0.02)
                new_mod = pa.load_loopback(monitor, target, latency_msec=30)
                time.sleep(VIRTUAL_SWITCH_MUTE_SEC)
            else:
                # For physical outputs keep make-before-break and a shorter mute window.
                new_mod = pa.load_loopback(monitor, target, latency_msec=30)
                pa.cleanup_wrong_loopbacks_for_source(monitor, target)
                time.sleep(PHYSICAL_SWITCH_MUTE_SEC)
        finally:
            # Ensure we never leave loopback inputs muted after the transition.
            for sid in prev_inputs:
                pa.set_sink_input_mute(sid, False)
            if new_mod:
                for sid in pa.sink_inputs_for_owner_module(new_mod):
                    pa.set_sink_input_mute(sid, False)
            pa.set_source_mute(monitor, False)
            pa.set_sink_mute(name, False)

        st["route_modules"][name] = new_mod
        st["route_target"][name] = target

    # ---------------------------------------------------------
    # 4) Ensure policy modules for role-based placement
    # ---------------------------------------------------------
    # Needed so streams with media.role=event/notification are opened
    # directly on sinks that advertise matching device.intended_roles.
    pa.ensure_module_loaded("module-intended-roles")

    # ---------------------------------------------------------
    # 5) Apply stream rules
    # ---------------------------------------------------------
    inputs = pa.list_sink_inputs()

    system_bus = "vsink.system"
    have_system_bus = pa.sink_exists(system_bus)
    sink_name_by_id = {str(s.get("id", "")).strip(): str(s.get("name", "")) for s in pa.list_sinks()}

    for inp in inputs:
        props = inp.get("props", {})
        app = (props.get("application.name") or "").lower()
        bin_ = (props.get("application.process.binary") or "").lower()
        aid = (props.get("pipewire.access.portal.app_id") or "").lower()

        matched_rule = False
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
                matched_rule = True
                try:
                    pa.move_sink_input(str(inp["id"]), tgt)
                except Exception:
                    pass

        if matched_rule:
            continue

        if have_system_bus and _is_system_stream(props):
            sid = str(inp.get("id", ""))
            sink_id = str(inp.get("sink_id", "")).strip()
            if sink_name_by_id.get(sink_id, "") == system_bus:
                continue
            trace(
                "system_stream_detected "
                f"sink_input={sid} "
                f"sink_id={inp.get('sink_id', '')} "
                f"app={props.get('application.name', '')} "
                f"binary={props.get('application.process.binary', '')} "
                f"media_role={props.get('media.role', '')} "
                f"media_name={props.get('media.name', '')}"
            )
            try:
                _move_input_quietly(sid, system_bus, mute_sec=SYSTEM_STREAM_MOVE_MUTE_SEC)
            except Exception as exc:
                trace(f"system_stream_move_error sink_input={sid} err={exc}")

    save_state(st)
