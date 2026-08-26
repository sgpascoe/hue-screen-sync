"""Load and save configuration from ~/.config/hue-screen-sync/config.toml."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hue-screen-sync"
CONFIG_PATH = CONFIG_DIR / "config.toml"


@dataclass
class BridgeConfig:
    ip: str = ""
    api_user: str = ""
    light_ids: list[int] = field(default_factory=list)


@dataclass
class CropConfig:
    left: int = 0    # percent from left edge
    right: int = 0
    top: int = 0
    bottom: int = 0


@dataclass
class SyncConfig:
    monitor_index: int = 1
    sample_hz: int = 10
    transition_time: int = 1
    smoothing: float = 0.35
    saturation: float = 1.8
    blur_radius: int = 0     # spatial blur before color extraction
    gamma: float = 1.8       # brightness curve (1.0=linear, higher=darker bias)
    crop: CropConfig = field(default_factory=CropConfig)


@dataclass
class BrightnessConfig:
    min_brightness: int = 15
    max_brightness: int = 100


@dataclass
class FavoriteColor:
    r: int = 255
    g: int = 255
    b: int = 255
    brightness: int = 127
    name: str = ""


@dataclass
class AppConfig:
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    night: BrightnessConfig = field(default_factory=lambda: BrightnessConfig(15, 100))
    day: BrightnessConfig = field(default_factory=lambda: BrightnessConfig(60, 200))
    favorites: list[FavoriteColor | None] = field(default_factory=lambda: [None] * 5)


def load_config() -> AppConfig:
    cfg = AppConfig()
    if not CONFIG_PATH.exists():
        return cfg

    try:
        with open(CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        return cfg

    if "bridge" in raw:
        b = raw["bridge"]
        ids = b.get("light_ids", b.get("light_id", [1]))
        if isinstance(ids, int):
            ids = [ids]
        cfg.bridge = BridgeConfig(
            ip=b.get("ip", ""),
            api_user=b.get("api_user", ""),
            light_ids=ids,
        )
    if "sync" in raw:
        s = raw["sync"]
        crop = CropConfig()
        if "crop" in s:
            c = s["crop"]
            crop = CropConfig(c.get("left", 0), c.get("right", 0),
                              c.get("top", 0), c.get("bottom", 0))
        cfg.sync = SyncConfig(
            monitor_index=s.get("monitor_index", 1),
            sample_hz=s.get("sample_hz", 10),
            transition_time=s.get("transition_time", 1),
            smoothing=s.get("smoothing", 0.35),
            saturation=s.get("saturation", 1.8),
            blur_radius=s.get("blur_radius", 0),
            gamma=s.get("gamma", 1.8),
            crop=crop,
        )
    if "night" in raw:
        n = raw["night"]
        cfg.night = BrightnessConfig(n.get("min_brightness", 15), n.get("max_brightness", 100))
    if "day" in raw:
        d = raw["day"]
        cfg.day = BrightnessConfig(d.get("min_brightness", 60), d.get("max_brightness", 200))

    if "favorites" in raw:
        favs = raw["favorites"]
        for i in range(5):
            key = f"slot{i}"
            if key in favs:
                s = favs[key]
                cfg.favorites[i] = FavoriteColor(
                    r=s.get("r", 255), g=s.get("g", 255),
                    b=s.get("b", 255), brightness=s.get("brightness", 127),
                    name=s.get("name", ""),
                )

    return cfg


def save_config(cfg: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ids_str = "[" + ", ".join(str(i) for i in cfg.bridge.light_ids) + "]"
    lines = [
        "[bridge]",
        f'ip = "{cfg.bridge.ip}"',
        f'api_user = "{cfg.bridge.api_user}"',
        f"light_ids = {ids_str}",
        "",
        "[sync]",
        f"monitor_index = {cfg.sync.monitor_index}",
        f"sample_hz = {cfg.sync.sample_hz}",
        f"transition_time = {cfg.sync.transition_time}",
        f"smoothing = {cfg.sync.smoothing}",
        f"saturation = {cfg.sync.saturation}",
        f"blur_radius = {cfg.sync.blur_radius}",
        f"gamma = {cfg.sync.gamma}",
        "",
        "[sync.crop]",
        f"left = {cfg.sync.crop.left}",
        f"right = {cfg.sync.crop.right}",
        f"top = {cfg.sync.crop.top}",
        f"bottom = {cfg.sync.crop.bottom}",
        "",
        "[night]",
        f"min_brightness = {cfg.night.min_brightness}",
        f"max_brightness = {cfg.night.max_brightness}",
        "",
        "[day]",
        f"min_brightness = {cfg.day.min_brightness}",
        f"max_brightness = {cfg.day.max_brightness}",
        "",
    ]
    fav_lines = ["", "[favorites]"]
    for i, fav in enumerate(cfg.favorites):
        if fav is not None:
            fav_lines.extend([
                f"[favorites.slot{i}]",
                f"r = {fav.r}",
                f"g = {fav.g}",
                f"b = {fav.b}",
                f"brightness = {fav.brightness}",
                f'name = "{fav.name}"',
            ])
    lines.extend(fav_lines)
    lines.append("")
    CONFIG_PATH.write_text("\n".join(lines))
    CONFIG_PATH.chmod(0o600)
