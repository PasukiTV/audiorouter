from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "audiorouter"
STATE_DIR  = Path(os.environ.get("XDG_STATE_HOME",  Path.home() / ".local" / "state")) / "audiorouter"

CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_PATH  = STATE_DIR / "state.json"

DEFAULT_CONFIG = {
    "buses": [],
    "rules": []
}

def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> Dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    return json.loads(CONFIG_PATH.read_text())

def save_config(cfg: Dict[str, Any]) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def load_state() -> Dict[str, Any]:
    ensure_dirs()
    if not STATE_PATH.exists():
        save_state({})
    return json.loads(STATE_PATH.read_text())

def save_state(st: Dict[str, Any]) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(st, indent=2))
