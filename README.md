
# audiorouter (WIP)

GUI + Hintergrund-Daemon zum Erstellen von virtuellen PipeWire/PulseAudio-Sinks ("Buses")
und zum automatischen Routen von App-Audio-Streams auf diese Buses.

## Dateien

- `audiorouter_gui.py` – GTK4/Libadwaita GUI
- `audiorouter_core.py` – **core**: `apply_once()` (idempotent)
- `audiorouter_daemon.py` – sparsamer Daemon: reagiert auf `pactl subscribe`
- `pactl.py` – pactl-Helfer
- `config.py` – Config/State JSON

## Dev-Run

```bash
chmod +x audiorouter_gui.py audiorouter_daemon.py
./audiorouter_gui.py
```

## Systemd User-Service (später Installation)

Die Unit liegt unter `systemd/audiorouter.service` und erwartet einen Launcher
unter `~/.local/bin/audiorouter-daemon` (siehe `bin/audiorouter-daemon`).

