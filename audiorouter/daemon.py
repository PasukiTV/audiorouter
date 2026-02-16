from __future__ import annotations

import atexit
import os
import signal
import subprocess
import time
import re
import threading
from pathlib import Path

from .core import apply_once, route_sink_input_now
from . import pactl as pa

_STOP = False
EVENT_DEBOUNCE_SEC = 0.25
MAINTENANCE_APPLY_SEC = 5.0


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
    try:
        apply_once()
    except Exception:
        pass


def _is_new_sink_input_event_line(line: str) -> bool:
    txt = line.lower()
    return "on sink-input" in txt and "'new'" in txt


_SINK_INPUT_ID_RE = re.compile(r"sink-input\s+#(\d+)", re.IGNORECASE)


def _sink_input_id_from_pulsectl_event(ev) -> str:
    idx = getattr(ev, "index", None)
    if idx is None:
        return ""
    try:
        return str(int(idx))
    except Exception:
        txt = str(idx).strip()
        return txt if txt.isdigit() else ""


def _sink_input_id_from_subscribe_line(line: str) -> str:
    m = _SINK_INPUT_ID_RE.search(line)
    return m.group(1) if m else ""


def _try_route_new_input_immediately(sink_input_id: str, reason: str) -> None:
    sid = str(sink_input_id).strip()
    if not sid:
        return
    try:
        route_sink_input_now(sid)
    except Exception:
        pass


def _is_new_pulsectl_event(ev) -> bool:
    """
    pulsectl event types are backend/version dependent.
    Depending on platform this may be an enum, string-like enum repr,
    or an int value. Normalize defensively so "new" events are recognized.
    """
    t = getattr(ev, "t", "")

    # Common case: enum-like object with a name attribute
    name = getattr(t, "name", None)
    if isinstance(name, str) and name.lower() == "new":
        return True

    # String/enum repr variants, e.g. "new", "PulseEventTypeEnum.new"
    txt = str(t).strip().lower()
    if txt == "new" or txt.endswith(".new"):
        return True

    # Fallback for int-like values (libpulse PA_SUBSCRIPTION_EVENT_NEW == 0x0000)
    try:
        return int(t) == 0
    except Exception:
        return False


def _scan_sink_input_ids() -> set[str]:
    ids: set[str] = set()
    try:
        for inp in pa.list_sink_inputs():
            sid = str(inp.get("id", "")).strip()
            if sid:
                ids.add(sid)
    except Exception:
        return set()
    return ids


def _watch_new_sink_inputs(poll_sec: float = 0.01) -> None:
    """
    Safety net: actively detect fresh sink-input IDs so short-lived system sounds
    can be routed even when event backends emit only delayed/"other" updates.
    """
    seen = _scan_sink_input_ids()
    while not _STOP:
        current = _scan_sink_input_ids()
        new_ids = current - seen
        for sid in sorted(new_ids):
            _try_route_new_input_immediately(sid, "poll:new")
        # Keep memory bounded to currently existing IDs
        seen = current
        time.sleep(poll_sec)


def run_daemon():

    # Single-instance guard
    if not _try_acquire_daemon_lock():
        return

    # 1) Wait for PipeWire/Pulse to be ready
    if not wait_for_pipewire():
        return

    # 2) Apply once initially
    _run_apply_once("startup")

    # 2b) Start active new-input watcher as safety net for delayed event backends
    threading.Thread(target=_watch_new_sink_inputs, name="audiorouter-new-input-watch", daemon=True).start()

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
                last_maintenance = 0.0

                # instead of "all": only what we need
                pulse.event_mask_set("sink_input")

                def cb(_ev):
                    nonlocal last, last_maintenance

                    if _is_new_pulsectl_event(_ev):
                        _try_route_new_input_immediately(_sink_input_id_from_pulsectl_event(_ev), "pulsectl:new")
                        _run_apply_once("pulsectl:new")
                        return

                    now = time.monotonic()
                    if now - last < EVENT_DEBOUNCE_SEC:
                        return
                    last = now

                    # "other" events are noisy and expensive if they trigger full
                    # reconciliation each time. Keep a low-rate maintenance pass.
                    if now - last_maintenance < MAINTENANCE_APPLY_SEC:
                        return
                    last_maintenance = now
                    _run_apply_once("pulsectl:maintenance")

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
            last_maintenance = 0.0
            while not _STOP and proc.stdout is not None:
                line = proc.stdout.readline()
                if not line:
                    break

                if _is_new_sink_input_event_line(line):
                    _try_route_new_input_immediately(_sink_input_id_from_subscribe_line(line), "subscribe:new")
                    _run_apply_once("subscribe:new")
                    continue

                now = time.monotonic()
                if now - last < EVENT_DEBOUNCE_SEC:
                    continue
                last = now
                if now - last_maintenance < MAINTENANCE_APPLY_SEC:
                    continue
                last_maintenance = now
                _run_apply_once("subscribe:maintenance")

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
