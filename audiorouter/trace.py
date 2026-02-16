from __future__ import annotations

import os
import time
from pathlib import Path

_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "audiorouter"
_TRACE_FILE = _CACHE_DIR / "trace.log"


def trace(msg: str) -> None:
    if os.environ.get("AUDIOROUTER_TRACE", "").lower() not in {"1", "true", "yes", "on"}:
        return

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        with _TRACE_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{ts:.6f} {msg}\n")
    except Exception:
        pass
