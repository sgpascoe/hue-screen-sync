# Hue Screen Sync

Sync your display's dominant color to Philips Hue bulbs for ambient lighting. Built for Linux (Mint priority).

## Features

- **Real-time screen sampling** — captures your display at ~10 Hz
- **Centre-weighted color extraction** — focuses on the action, ignores HUD/UI edges
- **Day/Night modes** — separate brightness curves for different times
- **System tray app** — left-click to toggle, right-click for settings
- **Settings UI** — configure bridge, brightness, smoothing, monitor selection
- **HUD filtering** — clips the top 2% brightest pixels to ignore UI overlays

## Setup

1. Copy the example config:
   ```bash
   mkdir -p ~/.config/hue-screen-sync
   cp config.example.toml ~/.config/hue-screen-sync/config.toml
   ```

2. Edit `~/.config/hue-screen-sync/config.toml` with your Hue Bridge IP and API user.
   - Find your bridge: https://discovery.meethue.com/
   - Create an API user: https://developers.meethue.com/develop/get-started-2/

3. Install and run:
   ```bash
   pip install .
   hue-screen-sync
   ```

   Or run directly:
   ```bash
   PYTHONPATH=src python3 -m hue_screen_sync.app
   ```

## Requirements

- Linux (X11) — tested on Linux Mint 22 Cinnamon
- Python 3.10+
- Philips Hue Bridge + color-capable bulb
- PySide6 or PyQt5

## License

MIT
