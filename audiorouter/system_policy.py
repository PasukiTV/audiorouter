from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _in_flatpak() -> bool:
    return bool(os.environ.get("FLATPAK_ID")) or Path("/.flatpak-info").exists()


def _run_host_cmd(cmd: list[str]) -> None:
    run_cmd = cmd
    if _in_flatpak():
        run_cmd = ["flatpak-spawn", "--host", *cmd]
    subprocess.run(run_cmd, check=False)


def _pipewire_pulse_conf_path() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return cfg / "pipewire" / "pipewire-pulse.conf.d" / "90-audiorouter-system-sounds.conf"


def install_system_sound_policy(target_sink: str = "vsink.system") -> Path:
    path = _pipewire_pulse_conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # PipeWire pulse stream rules: route event/notification streams at creation time.
    content = f"""pulse.rules = [
  {{
    matches = [
      {{ media.role = \"event\" }}
      {{ media.role = \"notification\" }}
      {{ application.name = \"Mutter\" media.name = \"bell-window-system\" }}
      {{ application.process.binary = \"gnome-shell\" media.name = \"bell-window-system\" }}
    ]
    actions = {{
      update-props = {{
        node.target = \"{target_sink}\"
        target.object = \"{target_sink}\"
      }}
    }}
  }}
]
"""
    path.write_text(content, encoding="utf-8")
    return path


def remove_system_sound_policy() -> Path:
    path = _pipewire_pulse_conf_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return path


def restart_pipewire_pulse() -> None:
    # Reload policy quickly; if user service manager is unavailable, this is best-effort.
    _run_host_cmd(["systemctl", "--user", "restart", "pipewire-pulse.service"])
