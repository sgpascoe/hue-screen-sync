"""Screen capture and Hue bridge sync engine — runs in a QThread."""

import json
import time
import urllib.request

import numpy as np
from mss import MSS as mss_cls

from .color import extract_scene_color, make_center_weights, apply_blur
from .config import AppConfig, BrightnessConfig

try:
    from PySide6.QtCore import QThread, Signal
except ImportError:
    from PyQt5.QtCore import QThread, pyqtSignal as Signal


class SyncThread(QThread):
    color_updated = Signal(float, float, int, int, int, int)  # x, y, bri, r, g, b
    error_occurred = Signal(str)

    def __init__(self, config: AppConfig, mode: str = "night"):
        super().__init__()
        self.config = config
        self.mode = mode
        self._running = False
        self.smooth_x = 0.3127
        self.smooth_y = 0.3290
        self.smooth_bri = 50.0
        self._pre_sync_states = {}

    @property
    def brightness_config(self) -> BrightnessConfig:
        return self.config.day if self.mode == "day" else self.config.night

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def _capture_light_states(self) -> None:
        cfg = self.config.bridge
        if not cfg.ip or not cfg.api_user:
            return
        for light_id in cfg.light_ids:
            url = f"http://{cfg.ip}/api/{cfg.api_user}/lights/{light_id}"
            try:
                resp = urllib.request.urlopen(url, timeout=2)
                data = json.loads(resp.read())
                state = data.get("state", {})
                restore = {"on": state.get("on", True)}
                cm = state.get("colormode", "xy")
                if cm == "ct" and "ct" in state:
                    restore["ct"] = state["ct"]
                elif "xy" in state:
                    restore["xy"] = state["xy"]
                if "bri" in state:
                    restore["bri"] = state["bri"]
                self._pre_sync_states[light_id] = restore
            except Exception:
                pass

    def _restore_light_states(self) -> None:
        cfg = self.config.bridge
        if not cfg.ip or not cfg.api_user:
            return
        for light_id, state in self._pre_sync_states.items():
            url = f"http://{cfg.ip}/api/{cfg.api_user}/lights/{light_id}/state"
            body = json.dumps({**state, "transitiontime": 10}).encode()
            req = urllib.request.Request(url, data=body, method="PUT")
            try:
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass

    def run(self) -> None:
        self._running = True
        cfg = self.config
        self._capture_light_states()

        try:
            with mss_cls() as sct:
                mon = sct.monitors[cfg.sync.monitor_index]
                w, h = mon["width"], mon["height"]
                step_y = max(1, h // 90)
                step_x = max(1, w // 160)

                while self._running:
                    t0 = time.monotonic()
                    interval = 1.0 / max(1, cfg.sync.sample_hz)
                    bcfg = self.brightness_config
                    crop = cfg.sync.crop

                    # capture and immediately downsample
                    img = sct.grab(mon)
                    frame = np.frombuffer(img.raw, dtype=np.uint8).reshape(h, w, 4)
                    small = frame[::step_y, ::step_x, :3][:, :, ::-1].copy()  # BGRA→RGB, downsampled
                    sh, sw = small.shape[:2]

                    # crop the small frame
                    cy0 = int(sh * crop.top / 100)
                    cy1 = sh - int(sh * crop.bottom / 100)
                    cx0 = int(sw * crop.left / 100)
                    cx1 = sw - int(sw * crop.right / 100)
                    if cy1 > cy0 and cx1 > cx0:
                        cropped = small[cy0:cy1, cx0:cx1]
                    else:
                        cropped = small
                    ch, cw_px = cropped.shape[:2]

                    # blur the small cropped frame (fast — it's only ~120x70)
                    if cfg.sync.blur_radius > 0:
                        cropped = apply_blur(cropped, cfg.sync.blur_radius)

                    # extract color
                    flat = cropped.reshape(-1, 3)
                    center_w = make_center_weights(cw_px, ch, 1)
                    x, y, bri, preview_rgb = extract_scene_color(
                        flat, center_w, bcfg.min_brightness, bcfg.max_brightness,
                        sat_boost=cfg.sync.saturation, gamma=cfg.sync.gamma,
                    )

                    s = cfg.sync.smoothing
                    self.smooth_x = float(self.smooth_x + s * (x - self.smooth_x))
                    self.smooth_y = float(self.smooth_y + s * (y - self.smooth_y))
                    self.smooth_bri = float(self.smooth_bri + s * (bri - self.smooth_bri))
                    final_bri = max(bcfg.min_brightness, int(self.smooth_bri))

                    # push to bridge
                    self._push_to_bridge(self.smooth_x, self.smooth_y, final_bri)

                    from .color import xy_to_rgb
                    sr, sg, sb = xy_to_rgb(self.smooth_x, self.smooth_y, final_bri)
                    self.color_updated.emit(
                        self.smooth_x, self.smooth_y, final_bri, sr, sg, sb,
                    )

                    elapsed = time.monotonic() - t0
                    remaining = interval - elapsed
                    if remaining > 0:
                        time.sleep(remaining)

        except Exception as e:
            self.error_occurred.emit(str(e))

    def stop(self) -> None:
        self._running = False
        self.wait(3000)
        self._restore_light_states()

    def _push_to_bridge(self, x: float, y: float, bri: int) -> None:
        cfg = self.config.bridge
        if not cfg.ip or not cfg.api_user:
            return

        # rate limit: Hue bridge supports ~10 commands/sec total
        now = time.monotonic()
        if hasattr(self, '_last_push') and (now - self._last_push) < 0.1:
            return
        self._last_push = now

        body = json.dumps({
            "xy": [round(x, 4), round(y, 4)],
            "bri": bri,
            "transitiontime": self.config.sync.transition_time,
        }).encode()
        for light_id in cfg.light_ids:
            url = f"http://{cfg.ip}/api/{cfg.api_user}/lights/{light_id}/state"
            req = urllib.request.Request(url, data=body, method="PUT")
            try:
                urllib.request.urlopen(req, timeout=0.5)
            except Exception:
                pass
