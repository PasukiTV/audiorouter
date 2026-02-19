#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from .system_policy import install_system_sound_policy, remove_system_sound_policy, restart_pipewire_pulse
from . import pactl as pa

# Make bundled modules importable inside Flatpak (if you bundle extra libs there)
LIBDIR = "/app/lib/audiorouter"
if os.path.isdir(LIBDIR) and LIBDIR not in sys.path:
    sys.path.insert(0, LIBDIR)


def main():
    if "--control-sink" in sys.argv:
        sink_name = ""
        action = ""
        value = ""
        for i, arg in enumerate(sys.argv):
            if arg == "--control-sink" and i + 1 < len(sys.argv):
                sink_name = sys.argv[i + 1].strip()
            elif arg == "--action" and i + 1 < len(sys.argv):
                action = sys.argv[i + 1].strip().lower()
            elif arg == "--value" and i + 1 < len(sys.argv):
                value = sys.argv[i + 1].strip()

        if not sink_name:
            print("Missing --control-sink <sink_name>", file=sys.stderr)
            sys.exit(2)
        if not pa.sink_exists(sink_name):
            print(f"Sink not found: {sink_name}", file=sys.stderr)
            sys.exit(1)

        if action == "set-volume":
            if not value:
                print("Missing --value for action set-volume", file=sys.stderr)
                sys.exit(2)
            pa.set_sink_volume(sink_name, value)
            print(f"Volume set: {sink_name} -> {value}")
            return

        if action == "change-volume":
            if not value:
                print("Missing --value for action change-volume", file=sys.stderr)
                sys.exit(2)
            pa.change_sink_volume(sink_name, value)
            print(f"Volume changed: {sink_name} {value}")
            return

        if action == "mute":
            pa.set_sink_mute(sink_name, True)
            print(f"Muted: {sink_name}")
            return

        if action == "unmute":
            pa.set_sink_mute(sink_name, False)
            print(f"Unmuted: {sink_name}")
            return

        if action == "toggle-mute":
            is_muted = pa.get_sink_mute(sink_name)
            pa.set_sink_mute(sink_name, not is_muted)
            print(f"Mute toggled: {sink_name} -> {'muted' if not is_muted else 'unmuted'}")
            return

        print(
            "Unknown or missing --action. Use: set-volume, change-volume, mute, unmute, toggle-mute",
            file=sys.stderr,
        )
        sys.exit(2)

    if "--install-system-policy" in sys.argv:
        target_sink = "vsink.system"
        for i, arg in enumerate(list(sys.argv)):
            if arg == "--system-policy-target" and i + 1 < len(sys.argv):
                target_sink = sys.argv[i + 1]
                break

        path = install_system_sound_policy(target_sink=target_sink)
        restart_pipewire_pulse()
        print(f"Installed system sound policy at: {path}")
        return

    if "--remove-system-policy" in sys.argv:
        path = remove_system_sound_policy()
        restart_pipewire_pulse()
        print(f"Removed system sound policy file: {path}")
        return

    daemon_mode = ("--daemon" in sys.argv) or ("--background" in sys.argv)

    if daemon_mode:
        from .daemon import run_daemon
        run_daemon()
        return

    # GUI mode
    os.environ.setdefault("GSK_RENDERER", "cairo")
    from .gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
