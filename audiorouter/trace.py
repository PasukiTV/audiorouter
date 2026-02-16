from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "audiorouter"
_TRACE_FILE = _CACHE_DIR / "trace.log"


def _trace_enabled() -> bool:
    return os.environ.get("AUDIOROUTER_TRACE", "").lower() in {"1", "true", "yes", "on"}


def _append_host_trace(line: str) -> None:
    """
    When running in Flatpak, also mirror trace lines to host ~/.cache/audiorouter/trace.log
    so debugging instructions work from the host shell.
    """
    if not os.environ.get("FLATPAK_ID"):
        return
    try:
        payload = shlex.quote(line)
        cmd = (
            "mkdir -p ~/.cache/audiorouter && "
            f"printf '%s\\n' {payload} >> ~/.cache/audiorouter/trace.log"
        )
        subprocess.run(
            ["flatpak-spawn", "--host", "sh", "-lc", cmd],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        )
    except Exception:
        pass


def trace(msg: str) -> None:
    if not _trace_enabled():
        return

    line = f"{time.time():.6f} {msg}"
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _TRACE_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")
    except Exception:
        pass

    _append_host_trace(line)
