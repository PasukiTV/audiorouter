# AudioRouter

AudioRouter is a modern Linux audio routing application designed for PipeWire (and PulseAudio).
It allows you to create virtual audio sinks (buses), route them to physical output devices,
and automatically assign applications to specific buses.

The application consists of a GTK-based GUI and a single-instance background daemon
that applies routing rules automatically.

![AudioRouter Screenshot](screenshots/audiorouter-main.png)

# Features

- Create and manage virtual audio sinks (buses)
- Route buses to any available output device
- Automatically route applications to specific buses
- Single-instance background daemon (no duplicate processes)
- Autostart support via GUI
- Designed for PipeWire-based desktops
- Flatpak / Flathub ready

# Requirements

- Linux system with PipeWire (recommended) or PulseAudio
- Flatpak
- Wayland or X11 desktop environment
- GTK-based desktop (tested on GNOME)

# Installation

## Installation via Flathub (recommended)

- Once available on Flathub, AudioRouter can be installed with:

    flatpak install flathub de.pasuki.audiorouter

- Run the application:

    flatpak run de.pasuki.audiorouter

## Manual Installation (from source)

- Clone the repository:

    git clone https://github.com/PasukiTV/audiorouter.git
    cd audiorouter

- Build and install the Flatpak locally:

    flatpak-builder --force-clean --user --install build-dir flatpak/de.pasuki.audiorouter.yml

- Run AudioRouter:

    flatpak run de.pasuki.audiorouter

## Background Daemon

AudioRouter uses a background daemon to apply audio routing rules automatically.

- To start the daemon manually:

    flatpak run de.pasuki.audiorouter --background

The daemon is locked to a single instance.
Starting it multiple times will not create duplicate background processes.

## Autostart

Autostart can be enabled from within the GUI.

When enabled, AudioRouter creates the following file:

    ~/.config/autostart/de.pasuki.audiorouter.autostart.desktop

This will automatically start the background daemon when you log in.

## Debugging

Check whether the background daemon is running:

    ps aux | grep audiorouter
    Stop all AudioRouter processes:
    pkill -f audiorouter

## License

MIT License

## Contributing

Contributions, bug reports and feature requests are welcome.
Please use the GitHub issue tracker for reporting problems or ideas.