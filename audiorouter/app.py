#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from .system_policy import install_system_sound_policy, remove_system_sound_policy, restart_pipewire_pulse

# Make bundled modules importable inside Flatpak (if you bundle extra libs there)
LIBDIR = "/app/lib/audiorouter"
if os.path.isdir(LIBDIR) and LIBDIR not in sys.path:
    sys.path.insert(0, LIBDIR)


def main():
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
