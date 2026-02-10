#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import threading

# Make bundled modules importable inside Flatpak
LIBDIR = "/app/lib/audiorouter"
if os.path.isdir(LIBDIR) and LIBDIR not in sys.path:
    sys.path.insert(0, LIBDIR)

def main():
    daemon_mode = ("--daemon" in sys.argv) or ("--background" in sys.argv)

    from audiorouter_daemon import run_daemon
    t = threading.Thread(target=run_daemon, daemon=False)
    t.start()

    if not daemon_mode:
        os.environ.setdefault("GSK_RENDERER", "cairo")
        from audiorouter_gui import main as gui_main
        gui_main()

    t.join()


if __name__ == "__main__":
    main()
