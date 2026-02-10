from pathlib import Path

AUTOSTART_FILE = (
    Path.home()
    / ".config"
    / "autostart"
    / "de.pasuki.audiorouter.desktop"
)

DESKTOP = """[Desktop Entry]
Type=Application
Name=AudioRouter (Background Service)
Exec=flatpak run de.pasuki.audiorouter --background
X-GNOME-Autostart-enabled=true
NoDisplay=true
"""

def is_enabled() -> bool:
    return AUTOSTART_FILE.exists()

def enable() -> None:
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(DESKTOP, encoding="utf-8")

def disable() -> None:
    try:
        AUTOSTART_FILE.unlink()
    except FileNotFoundError:
        pass

