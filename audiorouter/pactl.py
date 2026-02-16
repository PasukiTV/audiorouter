from __future__ import annotations
import os
import subprocess
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

def _in_flatpak() -> bool:
    return bool(os.environ.get("FLATPAK_ID")) or Path("/.flatpak-info").exists()

def _run_pactl(args: List[str]) -> Tuple[int, str, str]:
    cmd = ["pactl", *args]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    p = subprocess.run(cmd, text=True, capture_output=True)
    return p.returncode, p.stdout, p.stderr


def pactl(*args: str) -> str:
    rc, out, err = _run_pactl(list(args))
    if rc != 0:
        raise RuntimeError(err.strip() or "pactl failed")
    return out

def try_pactl(*args: str) -> str:
    rc, out, _ = _run_pactl(list(args))
    return out if rc == 0 else ""


def collect_debug_snapshot() -> str:
    sections = [
        ("info", ["info"]),
        ("default_sink", ["get-default-sink"]),
        ("sinks_short", ["list", "short", "sinks"]),
        ("sources_short", ["list", "short", "sources"]),
        ("modules_short", ["list", "short", "modules"]),
        ("sink_inputs", ["list", "sink-inputs"]),
    ]
    blocks = []
    for title, cmd in sections:
        blocks.append(f"## {title}")
        out = try_pactl(*cmd).strip()
        blocks.append(out or "(no output)")
        blocks.append("")
    return "\n".join(blocks)


def get_default_sink() -> str:
    return try_pactl("get-default-sink").strip()

def list_sinks() -> List[Dict[str, str]]:
    out = try_pactl("list", "short", "sinks")
    sinks = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append({"id": parts[0], "name": parts[1]})
    return sinks



def list_sink_descriptions() -> Dict[str, str]:
    out = try_pactl("list", "sinks")
    mapping: Dict[str, str] = {}
    cur_name = ""

    for raw in out.splitlines():
        line = raw.strip()

        if line.startswith("Name:"):
            cur_name = line.split(":", 1)[1].strip()
            if cur_name and cur_name not in mapping:
                mapping[cur_name] = cur_name
            continue

        if line.startswith("Description:") or line.startswith("Beschreibung:"):
            desc = line.split(":", 1)[1].strip()
            if cur_name and desc:
                mapping[cur_name] = desc

    return mapping

def list_sources() -> List[Dict[str, str]]:
    out = try_pactl("list", "short", "sources")
    srcs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            srcs.append({"id": parts[0], "name": parts[1]})
    return srcs

def list_modules() -> List[Dict[str, str]]:
    out = try_pactl("list", "short", "modules")
    mods = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            mods.append({"id": parts[0], "name": parts[1], "args": parts[2] if len(parts) > 2 else ""})
    return mods


def ensure_module_loaded(module_name: str, *module_args: str) -> None:
    for m in list_modules():
        if m.get("name") == module_name:
            return
    try_pactl("load-module", module_name, *module_args)


def sink_exists(name: str) -> bool:
    return any(s["name"] == name for s in list_sinks())

def source_exists(name: str) -> bool:
    return any(s["name"] == name for s in list_sources())



def set_source_mute(source_name: str, muted: bool) -> None:
    try_pactl("set-source-mute", source_name, "1" if muted else "0")


def set_sink_mute(sink_name: str, muted: bool) -> None:
    try_pactl("set-sink-mute", sink_name, "1" if muted else "0")


def set_sink_input_mute(sink_input_id: str, muted: bool) -> None:
    try_pactl("set-sink-input-mute", sink_input_id, "1" if muted else "0")


def tag_system_sink(sink_name: str = "vsink.system") -> None:
    """
    Hint Pulse/PipeWire to place event/notification streams on the system bus
    immediately at stream creation time.
    """
    if not sink_exists(sink_name):
        return
    # include both role names commonly used by Pulse/PipeWire clients
    try_pactl("set-sink-properties", sink_name, "device.intended_roles=event notification")


def unload_module(module_id: str) -> None:
    if module_id:
        try_pactl("unload-module", module_id)

def load_null_sink(bus_name: str, label: str) -> str:
    out = pactl(
        "load-module", "module-null-sink",
        f"sink_name={bus_name}",
        f"sink_properties=device.description={label}"
    )
    module_id = out.strip()

    # 🔒 Monitor-Source verstecken (GANZ WICHTIG)
    try:
        pactl(
            "set-source-properties",
            f"{bus_name}.monitor",
            "node.hidden=true",
            "node.passive=true"
        )
    except Exception:
        pass

    # 🔒 Sink selbst sauber markieren
    try:
        pactl(
            "set-sink-properties",
            bus_name,
            "media.class=Audio/Sink",
            "node.hidden=false"
        )
    except Exception:
        pass

    if bus_name == "vsink.system":
        tag_system_sink(bus_name)

    return module_id


def loopback_exists(source_name: str, sink_name: str) -> bool:
    for m in list_modules():
        if m.get("name") != "module-loopback":
            continue
        args = m.get("args", "") or ""
        if f"source={source_name}" in args and f"sink={sink_name}" in args:
            return True
    return False


def cleanup_wrong_loopbacks_for_source(source_name: str, wanted_sink: str) -> None:
    """
    Entfernt nur Loopbacks, die von source_name kommen, aber NICHT auf wanted_sink zeigen.
    Lässt das korrekte Loopback in Ruhe (wichtig gegen Create/Delete-Schleifen).
    """
    for m in list_modules():
        if m.get("name") != "module-loopback":
            continue
        args = m.get("args", "") or ""
        if f"source={source_name}" in args and f"sink={wanted_sink}" not in args:
            unload_module(m["id"])





def load_loopback(source_name: str, sink_name: str, latency_msec: int = 30) -> str:
    out = pactl(
        "load-module", "module-loopback",
        f"source={source_name}",
        f"sink={sink_name}",
        f"latency_msec={latency_msec}",
        "sink_dont_move=true",
    )
    module_id = out.strip()

    # Loopback-Knoten verstecken (PipeWire Name: loopback-<id>)
    loop_name = f"loopback-{module_id}"
    try:
        pactl("set-sink-properties", loop_name, "node.hidden=true", "node.passive=true")
    except Exception:
        pass
    try:
        pactl("set-source-properties", loop_name, "node.hidden=true", "node.passive=true")
    except Exception:
        pass

    return module_id



def move_sink_input(sink_input_id: str, target_sink: str) -> None:
    pactl("move-sink-input", sink_input_id, target_sink)

# list_sink_inputs: DE/EN + nur in Eigenschaften/Properties parsen
def list_sink_inputs() -> List[Dict[str, Any]]:
    out = try_pactl("list", "sink-inputs")
    items: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    in_props = False

    for raw in out.splitlines():
        line = raw.strip()

        if line.startswith("Sink Input #") or line.startswith("Ziel-Eingabe #"):
            if cur:
                items.append(cur)
            cur = {"id": line.split("#", 1)[1].strip(), "props": {}}
            in_props = False
            continue

        if cur is None:
            continue

        if line.startswith("Eigenschaften:") or line.startswith("Properties:"):
            in_props = True
            continue

        if line.startswith("Sink:") or line.startswith("Ziel:"):
            cur["sink_id"] = line.split(":", 1)[1].strip()
            continue

        if line.startswith("Owner Module:") or line.startswith("Besitzer-Modul:"):
            owner = line.split(":", 1)[1].strip()
            if owner not in ("n/a", "k. A."):
                cur["owner_module"] = owner
            continue

        if in_props and "=" in line:
            k, v = line.split("=", 1)
            cur["props"][k.strip()] = v.strip().strip('"')

    if cur:
        items.append(cur)

    return items

def sink_inputs_for_owner_module(module_id: str) -> List[str]:
    if not module_id:
        return []
    return [
        str(i.get("id", ""))
        for i in list_sink_inputs()
        if str(i.get("owner_module", "")) == str(module_id)
    ]


def get_physical_default_sink() -> str:
    default = get_default_sink()

    # Wenn Default kein vsink ist → ok
    if default and not default.startswith("vsink."):
        return default

    # Fallback: ersten echten Hardware-Sink nehmen
    for s in list_sinks():
        name = s["name"]
        if not name.startswith("vsink."):
            return name

    return default  # letzter fallback
