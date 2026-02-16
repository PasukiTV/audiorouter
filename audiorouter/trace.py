from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "audiorouter"
_TRACE_FILE = _CACHE_DIR / "trace.log"
_TRACE_ENABLE_FILE = _CACHE_DIR / "trace.enabled"


def _in_flatpak() -> bool:
    return bool(os.environ.get("FLATPAK_ID"))


def _run_host_shell(cmd: str, timeout: float = 0.8) -> subprocess.CompletedProcess[str] | None:
    if not _in_flatpak():
        return None
    try:
        return subprocess.run(
            ["flatpak-spawn", "--host", "sh", "-lc", cmd],
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except Exception:
        return None


def _append_host_trace(line: str) -> None:
    """
    When running in Flatpak, also mirror trace lines to host ~/.cache/audiorouter/trace.log
    so debugging instructions work from the host shell.
    """
    if not _in_flatpak():
        return
    payload = shlex.quote(line)
    _run_host_shell(
        "mkdir -p ~/.cache/audiorouter && "
        f"printf '%s\\n' {payload} >> ~/.cache/audiorouter/trace.log"
    )


def enable_trace_persisted() -> None:
    """
    Persist trace activation across daemon processes.
    Useful when `--trace` is invoked while another daemon instance already runs.
    """
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _TRACE_ENABLE_FILE.write_text("1\n", encoding="utf-8")
    except Exception:
        pass

    if _in_flatpak():
        _run_host_shell("mkdir -p ~/.cache/audiorouter && printf '1\\n' > ~/.cache/audiorouter/trace.enabled")


def disable_trace_persisted() -> None:
    try:
        _TRACE_ENABLE_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    if _in_flatpak():
        _run_host_shell("rm -f ~/.cache/audiorouter/trace.enabled")


def _trace_enabled() -> bool:
    if os.environ.get("AUDIOROUTER_TRACE", "").lower() in {"1", "true", "yes", "on"}:
        return True

    if _TRACE_ENABLE_FILE.exists():
        return True

    if _in_flatpak():
        res = _run_host_shell("test -f ~/.cache/audiorouter/trace.enabled && echo yes || true", timeout=0.4)
        if res and "yes" in (res.stdout or ""):
            return True

    return False


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
