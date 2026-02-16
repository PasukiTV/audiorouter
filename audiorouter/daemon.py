from __future__ import annotations

import atexit
import os
import signal
import subprocess
import time
from pathlib import Path

from .core import apply_once
from . import pactl as pa
from .trace import trace

_STOP = False
EVENT_DEBOUNCE_SEC = 0.05


def _handle_stop(_sig, _frame):
    global _STOP
    _STOP = True


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
LOCK_FILE = CACHE_DIR / "audiorouter-daemon.lock"



def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # If we can't signal it, it probably exists (or belongs to another user).
        return True


def _cleanup_lock() -> None:
    """Best-effort lock cleanup on exit (only remove if it is ours)."""
    try:
        txt = LOCK_FILE.read_text(encoding="utf-8").strip()
        if txt == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def _try_acquire_daemon_lock() -> bool:
    """
    Acquire single-instance lock for the daemon.
    Uses atomic O_EXCL create to avoid races (Flatpak-safe).
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Lock exists -> check if stale
        try:
            txt = LOCK_FILE.read_text(encoding="utf-8").strip()
            pid = int(txt) if txt else -1
        except Exception:
            # If we can't parse PID, assume locked (avoid race / accidental takeover)
            return False

        if _pid_alive(pid):
            return False

        # Stale lock -> remove and retry once
        try:
            LOCK_FILE.unlink()
        except Exception:
            return False

        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except Exception:
            return False

    # We own the lock -> write PID
    try:
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    finally:
        os.close(fd)

    atexit.register(_cleanup_lock)
    return True


def wait_for_pipewire(timeout: float = 15.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout and not _STOP:
        # Use shared pactl wrapper so Flatpak runs this via flatpak-spawn --host.
        if pa.try_pactl("info"):
            return True
        time.sleep(0.5)
    return False


def _run_apply_once(reason: str = "") -> None:
    if reason:
        trace(f"apply_once reason={reason}")
    try:
        apply_once()
    except Exception as exc:
        trace(f"apply_once_error reason={reason} err={exc}")


def _is_new_sink_input_event_line(line: str) -> bool:
    txt = line.lower()
    return "on sink-input" in txt and "'new'" in txt


def run_daemon():
    # Single-instance guard
    if not _try_acquire_daemon_lock():
        return

    # 1) Wait for PipeWire/Pulse to be ready
    if not wait_for_pipewire():
        return

    # 2) Apply once initially
    _run_apply_once("startup")

    # 3) Event-driven if pulsectl is available, otherwise fallback subscribe
    try:
        import pulsectl  # type: ignore
    except Exception:
        pulsectl = None

    if pulsectl is None:
        _fallback_subscribe()
        return

    # Reconnect loop (important!)
    while not _STOP:
        try:
            with pulsectl.Pulse("audiorouter-daemon") as pulse:
                last = 0.0

                # instead of "all": only what we need
                pulse.event_mask_set("sink_input")

                def cb(_ev):
                    nonlocal last

                    ev_type = str(getattr(_ev, "t", "")).lower()
                    if ev_type == "new":
                        _run_apply_once("pulsectl:new")
                        return

                    now = time.monotonic()
                    if now - last < EVENT_DEBOUNCE_SEC:
                        return
                    last = now
                    _run_apply_once("pulsectl:other")

                pulse.event_callback_set(cb)

                while not _STOP:
                    pulse.event_listen()

        except Exception:
            # PipeWire/Pulse was briefly unavailable -> wait and reconnect
            time.sleep(1.0)


def _fallback_poll():
    while not _STOP:
        try:
            apply_once()
        except Exception:
            pass
        time.sleep(1.0)


def _fallback_subscribe():
    """
    Event fallback without pulsectl.
    Uses `pactl subscribe` so new streams are moved quickly and don't audibly
    start on the default sink before rule-based routing applies.
    """
    while not _STOP:
        proc = None
        try:
            cmd = ["pactl", "subscribe"]
            if os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").exists():
                cmd = ["flatpak-spawn", "--host", *cmd]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            last = 0.0
            while not _STOP and proc.stdout is not None:
                line = proc.stdout.readline()
                if not line:
                    break

                trace(f"subscribe_event line={line.strip()}")

                if _is_new_sink_input_event_line(line):
                    _run_apply_once("subscribe:new")
                    continue

                now = time.monotonic()
                if now - last < EVENT_DEBOUNCE_SEC:
                    continue
                last = now
                _run_apply_once("subscribe:other")

        except Exception:
            pass
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    proc.kill()

        time.sleep(1.0)
