from __future__ import annotations

import subprocess
import time
import signal
from audiorouter_core import apply_once

_STOP = False

def _handle_stop(_sig, _frame):
    global _STOP
    _STOP = True

signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

def wait_for_pipewire(timeout: float = 15.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout and not _STOP:
        rc = subprocess.call(
            ["pactl", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if rc == 0:
            return True
        time.sleep(0.5)
    return False

def run_daemon():
    # 1) PipeWire/Pulse abwarten
    if not wait_for_pipewire():
        return

    # 2) einmal initial anwenden
    try:
        apply_once()
    except Exception:
        pass

    # 3) Event-driven, wenn pulsectl vorhanden
    try:
        import pulsectl
    except Exception:
        pulsectl = None

    if pulsectl is None:
        _fallback_poll()
        return

    # Reconnect-Loop (wichtig!)
    while not _STOP:
        try:
            with pulsectl.Pulse("audiorouter-daemon") as pulse:
                last = 0.0

                # statt "all": nur was wir brauchen
                pulse.event_mask_set("sink_input", "sink", "server")

                def cb(_ev):
                    nonlocal last
                    now = time.monotonic()
                    if now - last < 0.25:
                        return
                    last = now
                    try:
                        apply_once()
                    except Exception:
                        pass

                pulse.event_callback_set(cb)

                while not _STOP:
                    pulse.event_listen()

        except Exception:
            # PipeWire/Pulse war kurz weg → kurz warten und reconnect
            time.sleep(1.0)

def _fallback_poll():
    while not _STOP:
        try:
            apply_once()
        except Exception:
            pass
        time.sleep(1.0)
