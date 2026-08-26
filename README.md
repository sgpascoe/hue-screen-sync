# Hue Screen Sync

**Make your Philips Hue bulbs match whatever's on your screen.** Playing a game, watching a movie, or just browsing — your room lighting follows along in real time.

![Linux](https://img.shields.io/badge/Linux-X11-blue) ![Python](https://img.shields.io/badge/Python-3.10+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What It Does

Your screen is captured ~10 times per second. The app finds the dominant color, then sends it to your Hue bulbs. The result: your room glows whatever color your screen shows — reds during combat, blues during night scenes, greens in a forest.

It runs as a system tray app with a settings window. No terminal needed after install.

## Quick Start

### 1. Install

```bash
pip install git+https://github.com/sgpascoe/hue-screen-sync.git
```

Or clone and install locally:

```bash
git clone https://github.com/sgpascoe/hue-screen-sync.git
cd hue-screen-sync
pip install .
```

### 2. Run

```bash
hue-screen-sync
```

### 3. Connect Your Bridge

When the app opens, go to the **Bridge** tab:

1. Click **Auto-Discover** — it finds your Hue Bridge on your network
2. Press the physical button on your Hue Bridge
3. Click **Pair** in the app
4. Tick the bulbs you want to sync

That's it. Hit **Start Sync** and your lights follow your screen.

## What You Need

- **Linux** with X11 (tested on Linux Mint 22 Cinnamon, should work on Ubuntu/Fedora/etc)
- **Python 3.10** or newer
- **Philips Hue Bridge** (the square one that plugs into your router)
- **At least one color-capable Hue bulb** (White Ambiance or Color)

## Settings

Everything is adjustable through the app's UI — no config files to edit.

| Setting | What It Does |
|---------|-------------|
| **Saturation** | How vivid the colors are. Crank it up for games, down for movies |
| **Blur** | Smooths the screen sample. Higher = more averaged, less flickery |
| **Brightness bias** | Shifts the bulb brighter or darker than what the screen shows |
| **Crop** | Ignore edges of the screen (useful to skip HUD elements in games) |
| **Capture rate** | How often the screen is sampled (1-30 times per second) |
| **Responsiveness** | Low = gradual color changes, High = instant reaction |
| **Bulb fade** | How quickly the bulb transitions between colors |
| **Day/Night mode** | Separate brightness ranges for daytime and nighttime use |

Settings are saved to `~/.config/hue-screen-sync/config.toml`.

## Tips

- **For gaming:** Set saturation high, responsiveness high, and crop the HUD edges
- **For movies:** Lower the responsiveness for smoother transitions
- **Multiple monitors:** Pick which display to sync from in the Display tab
- **System tray:** Minimize to tray. Left-click the tray icon to show/hide, right-click for quick controls

## Manual Control

When sync is off, the **Manual** tab gives you direct bulb control:

- **Color wheel** — click to pick a color
- **RGB / Hue+Saturation / Hex / Color Temperature** sliders — all stay in sync
- **Brightness** — independent bulb dimming (1–254)
- **Favorites** — 5 slots you can save, rename (double-click), and drag to reorder. Persisted across sessions.

When you stop sync, bulbs return to whatever color they were before sync started.

## How It Works (The Short Version)

1. Grabs a screenshot of your chosen monitor
2. Downsamples and center-weights it (the middle of the screen matters more than edges)
3. Averages in OKLab perceptual color space for accurate blending
4. Applies your saturation/gamma/brightness settings
5. Converts to CIE xy color space (what Hue bulbs understand)
6. Sends it to the bridge via the local REST API
7. Repeats 10x per second

## License

MIT — do whatever you want with it.
