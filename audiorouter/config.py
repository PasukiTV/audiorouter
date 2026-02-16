from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "audiorouter"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "audiorouter"

# Legacy combined config (kept for backward compatibility)
CONFIG_PATH = CONFIG_DIR / "config.json"

# Preferred split config files for easier direct editing
VSINKS_PATH = CONFIG_DIR / "vsinks.json"
RULES_PATH = CONFIG_DIR / "routing-rules.json"

STATE_PATH = STATE_DIR / "state.json"

DEFAULT_CONFIG = {
    "buses": [],
    "rules": [],
    "mic_routes": [],
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def load_config() -> Dict[str, Any]:
    ensure_dirs()

    # Preferred: split files
    has_split = VSINKS_PATH.exists() or RULES_PATH.exists()
    if has_split:
        legacy = _read_json(CONFIG_PATH, {})
        cfg = {
            "buses": _read_json(VSINKS_PATH, []),
            "rules": _read_json(RULES_PATH, []),
            "mic_routes": legacy.get("mic_routes", []) if isinstance(legacy, dict) else [],
        }
        save_config(cfg)
        return cfg

    # Legacy fallback: combined config.json
    cfg = _read_json(CONFIG_PATH, None)
    if isinstance(cfg, dict):
        cfg.setdefault("buses", [])
        cfg.setdefault("rules", [])
        cfg.setdefault("mic_routes", [])
        save_config(cfg)
        return cfg

    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_dirs()

    normalized = {
        "buses": cfg.get("buses", []),
        "rules": cfg.get("rules", []),
        "mic_routes": cfg.get("mic_routes", []),
    }

    # Keep both split files and legacy config in sync.
    VSINKS_PATH.write_text(json.dumps(normalized["buses"], indent=2), encoding="utf-8")
    RULES_PATH.write_text(json.dumps(normalized["rules"], indent=2), encoding="utf-8")
    CONFIG_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def load_state() -> Dict[str, Any]:
    ensure_dirs()
    if not STATE_PATH.exists():
        save_state({})
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(st: Dict[str, Any]) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
