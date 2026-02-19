from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _http_timeout(value: Any, default: float = 2.0) -> float:
    try:
        val = float(value)
        if val <= 0:
            return default
        return val
    except Exception:
        return default


def companion_log_path() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    log_dir = state_home / "audiorouter"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "companion-sync.log"


def _log_line(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    try:
        with companion_log_path().open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # logging must never break audio control
        pass


def companion_enabled(cfg: Dict[str, Any]) -> bool:
    comp = cfg.get("companion", {}) if isinstance(cfg, dict) else {}
    if not isinstance(comp, dict):
        return False
    return bool(comp.get("enabled")) and bool(str(comp.get("url", "")).strip())


def sink_key_from_name(sink_name: str) -> str:
    name = (sink_name or "").strip()
    if name.startswith("vsink."):
        name = name[6:]
    parts = [p for p in name.replace("_", "-").split("-") if p]
    if not parts:
        return "sink"
    return parts[0].lower() + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _build_url(base_url: str, var_name: str, value: str) -> str:
    base = base_url.rstrip("/")
    path_var = urllib.parse.quote(var_name, safe="")
    q_val = urllib.parse.quote(str(value), safe="")
    return f"{base}/api/custom-variable/{path_var}/value?value={q_val}"


def _post_var(base_url: str, var_name: str, value: str, timeout_s: float) -> str:
    url = _build_url(base_url, var_name, value)
    req = urllib.request.Request(url=url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as res:
            code = getattr(res, "status", 200)
        return f"OK {code} POST {url}"
    except urllib.error.HTTPError as exc:
        return f"HTTP_ERROR {exc.code} POST {url} ({exc.reason})"
    except Exception as exc:
        return f"ERROR POST {url} ({exc})"


def push_sink_state(
    cfg: Dict[str, Any],
    sink_name: str,
    muted: bool | None = None,
    volume_percent: int | None = None,
    var_key_override: str | None = None,
) -> List[str]:
    """
    Push sink state to Bitfocus Companion custom variables.
    Returns debug lines of attempted requests / skips.
    """
    lines: List[str] = []
    comp = cfg.get("companion", {}) if isinstance(cfg, dict) else {}
    if not isinstance(comp, dict):
        lines.append("SKIP companion config missing/invalid")
        _log_line(lines[-1])
        return lines

    base_url = str(comp.get("url", "")).strip()
    if not comp.get("enabled"):
        lines.append("SKIP companion disabled")
        _log_line(lines[-1])
        return lines
    if not base_url:
        lines.append("SKIP companion url missing")
        _log_line(lines[-1])
        return lines

    key = (var_key_override or "").strip() or sink_key_from_name(sink_name)
    vol_suffix = str(comp.get("volume_suffix", "Vol")).strip() or "Vol"
    mute_suffix = str(comp.get("mute_suffix", "Mute")).strip() or "Mute"
    timeout_s = _http_timeout(comp.get("timeout_sec", 2.0), 2.0)

    lines.append(f"SYNC sink={sink_name} key={key} timeout={timeout_s}s")

    if volume_percent is not None:
        safe_vol = max(0, min(100, int(volume_percent)))
        var_name = f"{key}{vol_suffix}"
        lines.append(_post_var(base_url, var_name, str(safe_vol), timeout_s))

    if muted is not None:
        var_name = f"{key}{mute_suffix}"
        lines.append(_post_var(base_url, var_name, "1" if muted else "0", timeout_s))

    for ln in lines:
        _log_line(ln)
    return lines


def companion_defaults() -> Dict[str, Any]:
    return {
        "enabled": False,
        "url": "",
        "volume_suffix": "Vol",
        "mute_suffix": "Mute",
        "timeout_sec": 2.0,
    }


def normalize_companion_config(raw: Any) -> Dict[str, Any]:
    base = companion_defaults()
    if not isinstance(raw, dict):
        return base

    cfg = dict(base)
    cfg["enabled"] = bool(raw.get("enabled", False))
    cfg["url"] = str(raw.get("url", "")).strip()
    cfg["volume_suffix"] = str(raw.get("volume_suffix", "Vol")).strip() or "Vol"
    cfg["mute_suffix"] = str(raw.get("mute_suffix", "Mute")).strip() or "Mute"
    cfg["timeout_sec"] = _http_timeout(raw.get("timeout_sec", 2.0), 2.0)
    return cfg


def save_companion_config(cfg: Dict[str, Any], companion_cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg if isinstance(cfg, dict) else {})
    out["companion"] = normalize_companion_config(companion_cfg)
    return out
