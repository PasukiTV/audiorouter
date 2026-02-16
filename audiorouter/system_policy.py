from __future__ import annotations

import os
import subprocess
import shlex
from pathlib import Path


def _in_flatpak() -> bool:
    return bool(os.environ.get("FLATPAK_ID")) or Path("/.flatpak-info").exists()


def _run_host_cmd(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    run_cmd = cmd
    if _in_flatpak():
        run_cmd = ["flatpak-spawn", "--host", *cmd]
    return subprocess.run(run_cmd, check=False, text=True, input=input_text, capture_output=True)


def _host_home() -> str:
    if not _in_flatpak():
        return str(Path.home())
    p = _run_host_cmd(["sh", "-lc", 'printf %s "$HOME"'])
    home = (p.stdout or "").strip()
    return home or str(Path.home())


def _pipewire_pulse_conf_path() -> Path:
    return Path(_host_home()) / ".config" / "pipewire" / "pipewire-pulse.conf.d" / "90-audiorouter-system-sounds.conf"


def _write_file_host(path: Path, content: str) -> None:
    if not _in_flatpak():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return

    qp = shlex.quote(str(path.parent))
    qf = shlex.quote(str(path))
    cmd = [
        "sh",
        "-lc",
        f"mkdir -p {qp} && cat > {qf}",
    ]
    _run_host_cmd(cmd, input_text=content)


def _remove_file_host(path: Path) -> None:
    if not _in_flatpak():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    qf = shlex.quote(str(path))
    _run_host_cmd(["sh", "-lc", f"rm -f {qf}"])


def install_system_sound_policy(target_sink: str = "vsink.system") -> Path:
    path = _pipewire_pulse_conf_path()

    # stream.rules is the supported section for per-stream property rewrites.
    # This applies in pipewire-pulse context to Pulse clients (GNOME bell, etc.).
    content = f"""stream.rules = [
  {{
    matches = [
      {{ media.role = "event" }}
      {{ media.role = "notification" }}
      {{ media.role = "alarm" }}
      {{ media.name = "bell-window-system" }}
      {{ media.name = "~(?i).*(bell|notification|notify|system sound|system sounds|event|alert|benachrichtigung|systemklänge).*" }}
      {{ application.name = "Mutter" }}
      {{ application.name = "GNOME Shell" }}
      {{ application.process.binary = "gnome-shell" }}
      {{ application.process.binary = "canberra-gtk-play" }}
    ]
    actions = {{
      update-props = {{
        node.target = "{target_sink}"
        target.object = "{target_sink}"
      }}
    }}
  }}
]
"""
    _write_file_host(path, content)
    return path


def remove_system_sound_policy() -> Path:
    path = _pipewire_pulse_conf_path()
    _remove_file_host(path)
    return path


def restart_pipewire_pulse() -> None:
    # Reload policy quickly; if user service manager is unavailable, this is best-effort.
    _run_host_cmd(["systemctl", "--user", "restart", "pipewire-pulse.service"])


def system_sound_policy_installed() -> bool:
    path = _pipewire_pulse_conf_path()
    if not _in_flatpak():
        return path.exists()
    qf = shlex.quote(str(path))
    res = _run_host_cmd(["sh", "-lc", f"test -f {qf} && echo yes || true"])
    return (res.stdout or "").strip() == "yes"
