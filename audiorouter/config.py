from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .companion import normalize_companion_config

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "audiorouter"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "audiorouter"

# Legacy combined config (kept for backward compatibility)
CONFIG_PATH = CONFIG_DIR / "config.json"

# Preferred split config files for easier direct editing
VSINKS_PATH = CONFIG_DIR / "vsinks.json"
RULES_PATH = CONFIG_DIR / "routing-rules.json"
INPUT_RULES_PATH = CONFIG_DIR / "input-routes.json"

STATE_PATH = STATE_DIR / "state.json"

DEFAULT_CONFIG = {
    "buses": [],
    "rules": [],
    "mic_routes": [],
    "input_routes": [],
    "companion": normalize_companion_config(None),
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


def _normalize_config(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "buses": cfg.get("buses", []) if isinstance(cfg.get("buses", []), list) else [],
        "rules": cfg.get("rules", []) if isinstance(cfg.get("rules", []), list) else [],
        "mic_routes": cfg.get("mic_routes", []) if isinstance(cfg.get("mic_routes", []), list) else [],
        "input_routes": cfg.get("input_routes", []) if isinstance(cfg.get("input_routes", []), list) else [],
        "companion": normalize_companion_config(cfg.get("companion", None)),
    }


def load_config() -> Dict[str, Any]:
    ensure_dirs()

    # Preferred: split files
    has_split = VSINKS_PATH.exists() or RULES_PATH.exists() or INPUT_RULES_PATH.exists()
    if has_split:
        legacy = _read_json(CONFIG_PATH, {})
        cfg = _normalize_config({
            "buses": _read_json(VSINKS_PATH, []),
            "rules": _read_json(RULES_PATH, []),
            "mic_routes": legacy.get("mic_routes", []) if isinstance(legacy, dict) else [],
            "input_routes": _read_json(
                INPUT_RULES_PATH,
                legacy.get("input_routes", []) if isinstance(legacy, dict) else [],
            ),
            "companion": legacy.get("companion", None) if isinstance(legacy, dict) else None,
        })
        # Only sync files when split migration is incomplete.
        if not (VSINKS_PATH.exists() and RULES_PATH.exists() and INPUT_RULES_PATH.exists()):
            save_config(cfg)
        return cfg

    # Legacy fallback: combined config.json
    cfg_raw = _read_json(CONFIG_PATH, None)
    if isinstance(cfg_raw, dict):
        cfg = _normalize_config(cfg_raw)
        # Persist only when legacy content needed normalization.
        if cfg != cfg_raw:
            save_config(cfg)
        return cfg

    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_dirs()

    normalized = _normalize_config(cfg)

    # Keep both split files and legacy config in sync.
    VSINKS_PATH.write_text(json.dumps(normalized["buses"], indent=2), encoding="utf-8")
    RULES_PATH.write_text(json.dumps(normalized["rules"], indent=2), encoding="utf-8")
    INPUT_RULES_PATH.write_text(json.dumps(normalized["input_routes"], indent=2), encoding="utf-8")
    CONFIG_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def load_state() -> Dict[str, Any]:
    ensure_dirs()
    if not STATE_PATH.exists():
        save_state({})
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(st: Dict[str, Any]) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
