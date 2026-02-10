#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

# Make bundled modules importable inside Flatpak (if you bundle extra libs there)
LIBDIR = "/app/lib/audiorouter"
if os.path.isdir(LIBDIR) and LIBDIR not in sys.path:
    sys.path.insert(0, LIBDIR)


def main():
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
