"""Hue Screen Sync — main application with live preview and controls."""

import sys

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QSystemTrayIcon, QMenu, QWidget,
        QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QPushButton,
        QSlider, QLabel, QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox,
        QTabWidget, QFrame, QCheckBox,
    )
    from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QColor, QPen, QAction
    from PySide6.QtCore import Qt, QTimer
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QSystemTrayIcon, QMenu, QWidget,
        QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QPushButton,
        QSlider, QLabel, QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox,
        QTabWidget, QFrame, QAction, QCheckBox,
    )
    from PyQt5.QtGui import QIcon, QPixmap, QImage, QPainter, QColor, QPen
    from PyQt5.QtCore import Qt, QTimer

import numpy as np
from mss import MSS as mss_cls

import colorsys
import json
import math
import threading
import urllib.request

from .config import load_config, save_config, AppConfig, FavoriteColor
from .sync import SyncThread
from .color import make_center_weights, extract_scene_color, apply_blur, rgb_to_xy, xy_to_rgb
from .bridge import discover_bridges, create_api_user, get_color_lights

PREVIEW_W, PREVIEW_H = 320, 180
PREVIEW_FPS = 4


class PreviewWidget(QLabel):
    """Always-on live screen thumbnail with weight overlay."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #1a1a1a; border: 1px solid #333; border-radius: 4px;")

    def show_frame(self, rgb_array, crop_rect=None):
        """rgb_array: (h, w, 3) uint8 — what the sync actually sees (blurred if blur is on).
        crop_rect: (x0_frac, y0_frac, x1_frac, y1_frac) as 0-1 fractions."""
        h, w = rgb_array.shape[:2]
        display = np.ascontiguousarray(rgb_array)
        qimg = QImage(display.data, w, h, w * 3, QImage.Format.Format_RGB888)
        pm = QPixmap.fromImage(qimg).scaled(
            PREVIEW_W, PREVIEW_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        has_crop = crop_rect and (crop_rect[0] > 0.001 or crop_rect[1] > 0.001
                                   or crop_rect[2] < 0.999 or crop_rect[3] < 0.999)
        if has_crop:
            painter = QPainter(pm)
            pw, ph = pm.width(), pm.height()
            bx0 = int(crop_rect[0] * pw)
            by0 = int(crop_rect[1] * ph)
            bx1 = int(crop_rect[2] * pw)
            by1 = int(crop_rect[3] * ph)

            # dim outside crop
            painter.fillRect(0, 0, pw, by0, QColor(0, 0, 0, 120))
            painter.fillRect(0, by1, pw, ph - by1, QColor(0, 0, 0, 120))
            painter.fillRect(0, by0, bx0, by1 - by0, QColor(0, 0, 0, 120))
            painter.fillRect(bx1, by0, pw - bx1, by1 - by0, QColor(0, 0, 0, 120))

            # crop box
            pen = QPen(QColor(0, 180, 255, 220))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(bx0, by0, bx1 - bx0, by1 - by0)
            painter.end()

        self.setPixmap(pm)


def make_tray_icon(syncing: bool, mode: str) -> QIcon:
    """Draw a 22x22 bulb icon reflecting sync state and mode."""
    pm = QPixmap(22, 22)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if syncing:
        bulb_color = QColor(50, 140, 220) if mode == "night" else QColor(240, 190, 50)
        glow_color = QColor(30, 100, 180, 80) if mode == "night" else QColor(200, 160, 30, 80)
    else:
        bulb_color = QColor(60, 80, 100) if mode == "night" else QColor(120, 110, 70)
        glow_color = None

    # glow
    if glow_color:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow_color)
        p.drawEllipse(1, 1, 20, 20)

    # bulb body
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bulb_color)
    p.drawEllipse(4, 2, 14, 14)

    # bulb base
    base_color = QColor(160, 160, 160) if syncing else QColor(100, 100, 100)
    p.setBrush(base_color)
    p.drawRect(7, 15, 8, 5)

    p.end()
    return QIcon(pm)


class ColorSwatchWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedSize(80, 80)
        self.setStyleSheet("background: #333; border: 2px solid #555; border-radius: 40px;")

    def set_color(self, r, g, b):
        self.setStyleSheet(
            f"background: rgb({r},{g},{b}); border: 2px solid #555; border-radius: 40px;"
        )


def labeled_slider(label_text, min_val, max_val, value, callback, unit=""):
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(label_text)
    label.setFixedWidth(110)
    layout.addWidget(label)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(min_val, max_val)
    slider.setValue(value)
    layout.addWidget(slider)
    def fmt(v):
        sign = "+" if v > 0 and min_val < 0 else ""
        return f"{sign}{v}{unit}"

    val_label = QLabel(fmt(value))
    val_label.setFixedWidth(50)
    val_label.setAlignment(Qt.AlignmentFlag.AlignRight)
    layout.addWidget(val_label)

    def on_change(v):
        val_label.setText(fmt(v))
        callback(v)
    slider.valueChanged.connect(on_change)
    return container, slider


class ColorWheelWidget(QWidget):
    """HSV color wheel: hue ring with saturation as radius."""
    color_changed = None  # set by ManualTab

    def __init__(self, size=200):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self._hue = 0.0
        self._sat = 1.0
        self._wheel_pm = None
        self._build_wheel()

    def _build_wheel(self):
        s = self._size
        img = QImage(s, s, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        cx, cy = s / 2, s / 2
        radius = s / 2 - 2
        for py in range(s):
            for px in range(s):
                dx = px - cx
                dy = py - cy
                dist = math.sqrt(dx * dx + dy * dy)
                if dist <= radius:
                    angle = math.atan2(-dy, dx) / (2 * math.pi) % 1.0
                    sat = dist / radius
                    r, g, b = colorsys.hsv_to_rgb(angle, sat, 1.0)
                    img.setPixelColor(px, py, QColor(int(r * 255), int(g * 255), int(b * 255)))
        self._wheel_pm = QPixmap.fromImage(img)

    def set_color(self, hue, sat):
        self._hue = hue
        self._sat = sat
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._wheel_pm:
            p.drawPixmap(0, 0, self._wheel_pm)
        cx, cy = self._size / 2, self._size / 2
        radius = self._size / 2 - 2
        angle = self._hue * 2 * math.pi
        r = self._sat * radius
        mx = cx + r * math.cos(angle)
        my = cy - r * math.sin(angle)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(mx) - 6, int(my) - 6, 12, 12)
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.drawEllipse(int(mx) - 7, int(my) - 7, 14, 14)
        p.end()

    def mousePressEvent(self, event):
        self._pick(event.position() if hasattr(event, 'position') else event.pos())

    def mouseMoveEvent(self, event):
        self._pick(event.position() if hasattr(event, 'position') else event.pos())

    def _pick(self, pos):
        cx, cy = self._size / 2, self._size / 2
        radius = self._size / 2 - 2
        dx = pos.x() - cx
        dy = pos.y() - cy
        dist = min(math.sqrt(dx * dx + dy * dy), radius)
        self._hue = (math.atan2(-dy, dx) / (2 * math.pi)) % 1.0
        self._sat = dist / radius
        self.update()
        if self.color_changed:
            self.color_changed(self._hue, self._sat)


class ManualTab(QWidget):
    """Manual color control with multiple selection methods and favorites."""

    def __init__(self, config: AppConfig, push_callback):
        super().__init__()
        self._config = config
        self._push = push_callback
        self._suppressing = False
        self._r, self._g, self._b = 255, 180, 50
        self._brightness = 127
        self._apply_timer = QTimer()
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(100)
        self._apply_timer.timeout.connect(self._do_apply)

        layout = QVBoxLayout(self)

        # disabled overlay label
        self._disabled_label = QLabel(
            "Stop sync to use manual controls")
        self._disabled_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._disabled_label.setStyleSheet(
            "color: #e74c3c; font-size: 13px; font-weight: bold; padding: 8px;")
        self._disabled_label.hide()
        layout.addWidget(self._disabled_label)

        # main content
        self._content = QWidget()
        content_layout = QHBoxLayout(self._content)

        # left column: wheel + large swatch
        left = QVBoxLayout()
        self._wheel = ColorWheelWidget(180)
        self._wheel.color_changed = self._on_wheel_change
        left.addWidget(self._wheel, alignment=Qt.AlignmentFlag.AlignCenter)

        self._big_swatch = QFrame()
        self._big_swatch.setFixedSize(180, 60)
        self._big_swatch.setStyleSheet("border-radius: 8px; border: 2px solid #555;")
        left.addWidget(self._big_swatch, alignment=Qt.AlignmentFlag.AlignCenter)

        # hex input
        hex_row = QHBoxLayout()
        hex_label = QLabel("Hex:")
        hex_label.setFixedWidth(30)
        hex_row.addWidget(hex_label)
        self._hex_edit = QLineEdit("#FFB432")
        self._hex_edit.setFixedWidth(90)
        self._hex_edit.setMaxLength(7)
        self._hex_edit.editingFinished.connect(self._on_hex_input)
        hex_row.addWidget(self._hex_edit)
        hex_row.addStretch()
        left.addLayout(hex_row)
        left.addStretch()
        content_layout.addLayout(left)

        # right column: all sliders
        right = QVBoxLayout()

        # brightness (independent, at the top)
        bri_group = QGroupBox("Brightness")
        bri_layout = QVBoxLayout(bri_group)
        c_bri, self._bri_slider = labeled_slider("Brightness", 1, 254, self._brightness,
                                                  self._on_bri_slider)
        bri_layout.addWidget(c_bri)
        right.addWidget(bri_group)

        # RGB sliders
        rgb_group = QGroupBox("RGB")
        rgb_layout = QVBoxLayout(rgb_group)
        c_r, self._r_slider = labeled_slider("Red", 0, 255, self._r, self._on_rgb_slider)
        c_g, self._g_slider = labeled_slider("Green", 0, 255, self._g, self._on_rgb_slider)
        c_b, self._b_slider = labeled_slider("Blue", 0, 255, self._b, self._on_rgb_slider)
        rgb_layout.addWidget(c_r)
        rgb_layout.addWidget(c_g)
        rgb_layout.addWidget(c_b)
        right.addWidget(rgb_group)

        # HSL sliders
        hsl_group = QGroupBox("HSL")
        hsl_layout = QVBoxLayout(hsl_group)
        c_hue, self._hue_slider = labeled_slider("Hue", 0, 360, 30, self._on_hsl_slider, unit="deg")
        c_sat, self._sat_slider = labeled_slider("Saturation", 0, 100, 100, self._on_hsl_slider, unit="%")
        c_lit, self._lit_slider = labeled_slider("Lightness", 0, 100, 50, self._on_hsl_slider, unit="%")
        hsl_layout.addWidget(c_hue)
        hsl_layout.addWidget(c_sat)
        hsl_layout.addWidget(c_lit)
        right.addWidget(hsl_group)

        # color temperature slider
        temp_group = QGroupBox("Color Temperature")
        temp_layout = QVBoxLayout(temp_group)
        c_temp, self._temp_slider = labeled_slider("Warm / Cool", 2000, 6500, 3500,
                                                    self._on_temp_slider, unit="K")
        temp_layout.addWidget(c_temp)
        temp_desc = QLabel("2000K = candlelight, 4000K = neutral, 6500K = daylight")
        temp_desc.setStyleSheet("color: #a0a0a0; font-size: 10px;")
        temp_layout.addWidget(temp_desc)
        right.addWidget(temp_group)

        content_layout.addLayout(right)
        layout.addWidget(self._content)

        # favorites row
        fav_group = QGroupBox("Favorites")
        fav_layout = QHBoxLayout(fav_group)
        self._fav_swatches = []
        self._fav_save_btns = []
        for i in range(5):
            slot = QVBoxLayout()
            swatch = QPushButton()
            swatch.setFixedSize(48, 48)
            swatch.setStyleSheet("border-radius: 8px; border: 2px solid #555; background: #333;")
            swatch.setToolTip(f"Click to load favorite {i + 1}")
            swatch.clicked.connect(lambda _, idx=i: self._load_fav(idx))
            slot.addWidget(swatch, alignment=Qt.AlignmentFlag.AlignCenter)
            save_btn = QPushButton("Save")
            save_btn.setFixedWidth(48)
            save_btn.setStyleSheet("font-size: 10px; padding: 2px; background: #444;")
            save_btn.clicked.connect(lambda _, idx=i: self._save_fav(idx))
            slot.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            fav_layout.addLayout(slot)
            self._fav_swatches.append(swatch)
            self._fav_save_btns.append(save_btn)
        layout.addWidget(fav_group)

        # live indicator
        self._live_label = QLabel("Changes apply to bulbs live")
        self._live_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live_label.setStyleSheet(
            "color: #27ae60; font-size: 11px; font-style: italic; padding: 4px;")
        layout.addWidget(self._live_label)

        self._refresh_favorites()
        self._update_all_from_rgb()

    def set_enabled_state(self, sync_active: bool):
        self._content.setEnabled(not sync_active)
        for btn in self._fav_save_btns:
            btn.setEnabled(not sync_active)
        for sw in self._fav_swatches:
            sw.setEnabled(not sync_active)
        self._disabled_label.setVisible(sync_active)
        self._live_label.setVisible(not sync_active)

    def _on_wheel_change(self, hue, sat):
        if self._suppressing:
            return
        r, g, b = colorsys.hsv_to_rgb(hue, sat, 1.0)
        self._r, self._g, self._b = int(r * 255), int(g * 255), int(b * 255)
        self._suppressing = True
        self._sync_sliders_from_rgb()
        self._suppressing = False
        self._update_display()

    def _on_rgb_slider(self, _=None):
        if self._suppressing:
            return
        self._r = self._r_slider.value()
        self._g = self._g_slider.value()
        self._b = self._b_slider.value()
        self._suppressing = True
        self._sync_wheel_from_rgb()
        self._sync_hsl_from_rgb()
        self._suppressing = False
        self._update_display()

    def _on_hsl_slider(self, _=None):
        if self._suppressing:
            return
        h = self._hue_slider.value() / 360.0
        s = self._sat_slider.value() / 100.0
        l = self._lit_slider.value() / 100.0
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        self._r, self._g, self._b = int(r * 255), int(g * 255), int(b * 255)
        self._suppressing = True
        self._sync_rgb_sliders()
        self._sync_wheel_from_rgb()
        self._suppressing = False
        self._update_display()

    def _on_temp_slider(self, kelvin):
        if self._suppressing:
            return
        r, g, b = self._kelvin_to_rgb(kelvin)
        self._r, self._g, self._b = r, g, b
        self._suppressing = True
        self._sync_sliders_from_rgb()
        self._suppressing = False
        self._update_display()

    def _on_bri_slider(self, val):
        self._brightness = val
        self._update_display()

    def _on_hex_input(self):
        text = self._hex_edit.text().strip().lstrip("#")
        if len(text) == 6:
            try:
                self._r = int(text[0:2], 16)
                self._g = int(text[2:4], 16)
                self._b = int(text[4:6], 16)
                self._suppressing = True
                self._sync_sliders_from_rgb()
                self._suppressing = False
                self._update_display()
            except ValueError:
                pass

    def _sync_sliders_from_rgb(self):
        self._sync_rgb_sliders()
        self._sync_hsl_from_rgb()
        self._sync_wheel_from_rgb()

    def _sync_rgb_sliders(self):
        self._r_slider.setValue(self._r)
        self._g_slider.setValue(self._g)
        self._b_slider.setValue(self._b)

    def _sync_hsl_from_rgb(self):
        r, g, b = self._r / 255.0, self._g / 255.0, self._b / 255.0
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        self._hue_slider.setValue(int(h * 360))
        self._sat_slider.setValue(int(s * 100))
        self._lit_slider.setValue(int(l * 100))

    def _sync_wheel_from_rgb(self):
        r, g, b = self._r / 255.0, self._g / 255.0, self._b / 255.0
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        self._wheel.set_color(h, s)

    def _update_all_from_rgb(self):
        self._suppressing = True
        self._sync_sliders_from_rgb()
        self._suppressing = False
        self._update_display()

    def _update_display(self):
        scale = self._brightness / 254.0
        dr = int(self._r * scale)
        dg = int(self._g * scale)
        db = int(self._b * scale)
        self._big_swatch.setStyleSheet(
            f"background: rgb({dr},{dg},{db}); "
            "border-radius: 8px; border: 2px solid #555;")
        self._hex_edit.setText(f"#{self._r:02x}{self._g:02x}{self._b:02x}")
        self._apply_to_bulbs()

    def _apply_to_bulbs(self):
        if not self._apply_timer.isActive():
            self._apply_timer.start()

    def _do_apply(self):
        r, g, b = self._r / 255.0, self._g / 255.0, self._b / 255.0
        if max(r, g, b) < 0.004:
            self._push(0.3127, 0.3290, 1)
            return
        x, y = rgb_to_xy(r, g, b)
        self._push(x, y, self._brightness)

    def _save_fav(self, idx):
        self._config.favorites[idx] = FavoriteColor(
            r=self._r, g=self._g, b=self._b, brightness=self._brightness)
        self._refresh_favorites()

    def _load_fav(self, idx):
        fav = self._config.favorites[idx]
        if fav is None:
            return
        self._r, self._g, self._b = fav.r, fav.g, fav.b
        self._brightness = fav.brightness
        self._bri_slider.setValue(self._brightness)
        self._update_all_from_rgb()

    def _refresh_favorites(self):
        for i, fav in enumerate(self._config.favorites):
            if fav is not None:
                self._fav_swatches[i].setStyleSheet(
                    f"background: rgb({fav.r},{fav.g},{fav.b}); "
                    "border-radius: 8px; border: 2px solid #555;")
            else:
                self._fav_swatches[i].setStyleSheet(
                    "background: #333; border-radius: 8px; border: 2px dashed #555;")

    @staticmethod
    def _kelvin_to_rgb(kelvin):
        """Attempt the Tanner Helland algorithm for color temperature to RGB."""
        temp = kelvin / 100.0
        if temp <= 66:
            r = 255
            g = max(0, min(255, int(99.4708025861 * math.log(temp) - 161.1195681661)))
            if temp <= 19:
                b = 0
            else:
                b = max(0, min(255, int(138.5177312231 * math.log(temp - 10) - 305.0447927307)))
        else:
            r = max(0, min(255, int(329.698727446 * ((temp - 60) ** -0.1332047592))))
            g = max(0, min(255, int(288.1221695283 * ((temp - 60) ** -0.0755148492))))
            b = 255
        return r, g, b


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.sync_thread = None
        self.mode = "night"
        self._sct = None

        self.setWindowTitle("Hue Screen Sync")
        self.setMinimumWidth(500)
        self._apply_style()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # top row: preview + color swatch
        top_row = QHBoxLayout()
        self.preview = PreviewWidget()
        top_row.addWidget(self.preview)

        right_col = QVBoxLayout()
        self.swatch = ColorSwatchWidget()
        right_col.addWidget(self.swatch, alignment=Qt.AlignmentFlag.AlignCenter)

        info_style = "color: #999; font-size: 10px; font-family: monospace;"
        self.hue_label = QLabel("hue  —")
        self.hue_label.setStyleSheet(info_style)
        right_col.addWidget(self.hue_label)
        self.sat_label = QLabel("sat  —")
        self.sat_label.setStyleSheet(info_style)
        right_col.addWidget(self.sat_label)
        self.bri_label = QLabel("bri  —")
        self.bri_label.setStyleSheet(info_style)
        right_col.addWidget(self.bri_label)
        self.xy_label = QLabel("xy   —")
        self.xy_label.setStyleSheet(info_style)
        right_col.addWidget(self.xy_label)
        self.rgb_label = QLabel("rgb  —")
        self.rgb_label.setStyleSheet(info_style)
        right_col.addWidget(self.rgb_label)

        self.status_label = QLabel("Preview only")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        right_col.addWidget(self.status_label)
        right_col.addStretch()
        top_row.addLayout(right_col)
        main_layout.addLayout(top_row)

        # toggle + mode buttons
        btn_row = QHBoxLayout()
        self.toggle_btn = QPushButton("Start Sync")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self._toggle_sync)
        btn_row.addWidget(self.toggle_btn)
        self.mode_btn = QPushButton("Toggle Day / Night")
        self.mode_btn.setFixedWidth(160)
        self.mode_btn.clicked.connect(self._cycle_mode)
        btn_row.addWidget(self.mode_btn)
        main_layout.addLayout(btn_row)

        # mode indicator — not a button, clearly a status bar
        self.mode_indicator = QLabel()
        self.mode_indicator.setFixedHeight(28)
        self.mode_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_mode_indicator()
        main_layout.addWidget(self.mode_indicator)

        # tabs
        tabs = QTabWidget()

        # brightness tab
        bri_tab = QWidget()
        bri_layout = QVBoxLayout(bri_tab)
        night_group = QGroupBox("Night Mode")
        nl = QVBoxLayout(night_group)
        c1, _ = labeled_slider("Min Bri", 1, 254, config.night.min_brightness,
                               lambda v: setattr(config.night, "min_brightness", v))
        c2, _ = labeled_slider("Max Bri", 1, 254, config.night.max_brightness,
                               lambda v: setattr(config.night, "max_brightness", v))
        nl.addWidget(c1); nl.addWidget(c2)
        bri_layout.addWidget(night_group)
        day_group = QGroupBox("Day Mode")
        dl = QVBoxLayout(day_group)
        c3, _ = labeled_slider("Min Bri", 1, 254, config.day.min_brightness,
                               lambda v: setattr(config.day, "min_brightness", v))
        c4, _ = labeled_slider("Max Bri", 1, 254, config.day.max_brightness,
                               lambda v: setattr(config.day, "max_brightness", v))
        dl.addWidget(c3); dl.addWidget(c4)
        bri_layout.addWidget(day_group)
        tabs.addTab(bri_tab, "Brightness")

        # display tab
        display_tab = QWidget()
        display_layout = QVBoxLayout(display_tab)

        screen_group = QGroupBox("Screen")
        sg_layout = QFormLayout(screen_group)
        self.monitor_combo = QComboBox()
        self._populate_monitors()
        sg_layout.addRow("Display:", self.monitor_combo)
        display_layout.addWidget(screen_group)

        color_group = QGroupBox("Color")
        cg_layout = QVBoxLayout(color_group)

        # saturation: -100% (desaturated) to +100% (vivid), 0 = screen-accurate
        sat_slider_val = self._boost_to_slider(config.sync.saturation)
        c_sat, self._sat_slider = labeled_slider(
            "Saturation", -100, 100, sat_slider_val,
            lambda v: setattr(config.sync, "saturation", self._slider_to_boost(v)),
            unit="%")
        cg_layout.addWidget(c_sat)

        # blur: 0-100% (none to full single-color blend)
        c_blur, _ = labeled_slider("Blur", 0, 100, config.sync.blur_radius,
                                   lambda v: setattr(config.sync, "blur_radius", v),
                                   unit="%")
        cg_layout.addWidget(c_blur)

        # brightness bias: -100 (darker) to +100 (brighter), 0 = neutral
        gamma_slider_val = int((1.8 - config.sync.gamma) / 1.8 * 100)  # 1.8=neutral→0
        c_gamma, _ = labeled_slider("Bri bias", -100, 100, gamma_slider_val,
                                    lambda v: setattr(config.sync, "gamma", 1.8 - v * 1.8 / 100),
                                    unit="%")
        cg_layout.addWidget(c_gamma)
        display_layout.addWidget(color_group)

        crop_group = QGroupBox("Crop")
        crop_layout = QVBoxLayout(crop_group)
        c_top, _ = labeled_slider("Top", 0, 45, config.sync.crop.top,
                                  lambda v: setattr(config.sync.crop, "top", v), unit="%")
        c_btm, _ = labeled_slider("Bottom", 0, 45, config.sync.crop.bottom,
                                  lambda v: setattr(config.sync.crop, "bottom", v), unit="%")
        c_left, _ = labeled_slider("Left", 0, 45, config.sync.crop.left,
                                   lambda v: setattr(config.sync.crop, "left", v), unit="%")
        c_right, _ = labeled_slider("Right", 0, 45, config.sync.crop.right,
                                    lambda v: setattr(config.sync.crop, "right", v), unit="%")
        crop_layout.addWidget(c_top)
        crop_layout.addWidget(c_btm)
        crop_layout.addWidget(c_left)
        crop_layout.addWidget(c_right)
        display_layout.addWidget(crop_group)
        tabs.addTab(display_tab, "Display")

        # sync tab
        sync_tab = QWidget()
        sync_layout = QVBoxLayout(sync_tab)
        timing_group = QGroupBox("Timing")
        tg_layout = QVBoxLayout(timing_group)

        desc_style = "color: #a0a0a0; font-size: 11px; margin-left: 100px;"

        c6, _ = labeled_slider("Capture rate", 1, 30, config.sync.sample_hz,
                               lambda v: setattr(config.sync, "sample_hz", v))
        tg_layout.addWidget(c6)
        fps_desc = QLabel("How many times per second the screen is sampled")
        fps_desc.setStyleSheet(desc_style)
        tg_layout.addWidget(fps_desc)

        c5, _ = labeled_slider("Responsiveness", 5, 100, int(config.sync.smoothing * 100),
                               lambda v: setattr(config.sync, "smoothing", v / 100.0))
        tg_layout.addWidget(c5)
        smooth_desc = QLabel("Low = gradual color shifts, High = instant reaction")
        smooth_desc.setStyleSheet(desc_style)
        tg_layout.addWidget(smooth_desc)

        c7, _ = labeled_slider("Bulb fade", 0, 20, config.sync.transition_time,
                               lambda v: setattr(config.sync, "transition_time", v))
        tg_layout.addWidget(c7)
        fade_desc = QLabel("How quickly the bulb transitions (×100ms, 0 = instant)")
        fade_desc.setStyleSheet(desc_style)
        tg_layout.addWidget(fade_desc)

        sync_layout.addWidget(timing_group)
        sync_layout.addStretch()
        tabs.addTab(sync_tab, "Sync")

        # bridge tab — auto-discovery
        bridge_tab = QWidget()
        bridge_layout = QVBoxLayout(bridge_tab)

        disc_group = QGroupBox("Bridge Connection")
        disc_layout = QFormLayout(disc_group)
        self.ip_edit = QLineEdit(config.bridge.ip)
        self.ip_edit.textChanged.connect(lambda t: setattr(config.bridge, "ip", t))
        disc_layout.addRow("Bridge IP:", self.ip_edit)

        discover_btn = QPushButton("Auto-Discover")
        discover_btn.clicked.connect(self._discover_bridge)
        disc_layout.addRow(discover_btn)

        self.api_edit = QLineEdit(config.bridge.api_user)
        self.api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_edit.textChanged.connect(lambda t: setattr(config.bridge, "api_user", t))
        disc_layout.addRow("API Key:", self.api_edit)

        pair_btn = QPushButton("Pair (press bridge button first)")
        pair_btn.clicked.connect(self._pair_bridge)
        disc_layout.addRow(pair_btn)

        self.bridge_status = QLabel("" if config.bridge.ip else "No bridge configured")
        self.bridge_status.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        disc_layout.addRow(self.bridge_status)
        bridge_layout.addWidget(disc_group)

        light_group = QGroupBox("Lights")
        light_layout = QVBoxLayout(light_group)
        self.light_list = QLabel(self._format_light_ids())
        self.light_list.setStyleSheet("color: #aaa; font-size: 11px;")
        light_layout.addWidget(self.light_list)

        light_btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Lights")
        refresh_btn.clicked.connect(self._refresh_lights)
        light_btn_row.addWidget(refresh_btn)
        light_layout.addLayout(light_btn_row)

        self.light_checks_container = QWidget()
        self.light_checks_layout = QVBoxLayout(self.light_checks_container)
        self.light_checks_layout.setContentsMargins(0, 0, 0, 0)
        light_layout.addWidget(self.light_checks_container)
        bridge_layout.addWidget(light_group)
        bridge_layout.addStretch()
        tabs.addTab(bridge_tab, "Bridge")

        # manual tab
        self.manual_tab = ManualTab(config, self._manual_push)
        tabs.addTab(self.manual_tab, "Manual")

        main_layout.addWidget(tabs)

        self._save_btn = QPushButton("Save Settings")
        self._save_btn.setStyleSheet("background: #27ae60;")
        self._save_btn.clicked.connect(self._save)
        main_layout.addWidget(self._save_btn)

        # tray icon
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(make_tray_icon(False, self.mode), self)
            self.tray.setToolTip("Hue Screen Sync")
            self._tray_menu = QMenu(self)
            self._tray_status = self._tray_menu.addAction("Sync: off  |  Night mode")
            self._tray_status.setEnabled(False)
            self._tray_menu.addSeparator()
            self._tray_sync = self._tray_menu.addAction("Start Sync")
            self._tray_sync.triggered.connect(self._toggle_sync)
            self._tray_mode = self._tray_menu.addAction("Switch to Day")
            self._tray_mode.triggered.connect(self._cycle_mode)
            self._tray_menu.addSeparator()
            self._tray_show = self._tray_menu.addAction("Show Window")
            self._tray_show.triggered.connect(self._show_window)
            self._tray_quit = self._tray_menu.addAction("Quit")
            self._tray_quit.triggered.connect(self.quit_app)
            self.tray.setContextMenu(self._tray_menu)
            self.tray.activated.connect(self._on_tray_click)
            self.tray.show()

        # always-on preview timer
        self._preview_timer = QTimer()
        self._preview_timer.timeout.connect(self._capture_preview)
        self._preview_timer.start(1000 // PREVIEW_FPS)

    @staticmethod
    def _slider_to_boost(v):
        """Convert slider -100..+100 to sat_boost 0.0..5.0, with 0 = 1.0 (neutral)."""
        if v <= 0:
            return max(0.0, 1.0 + v / 100.0)  # -100→0.0, 0→1.0
        return 1.0 + v * 4.0 / 100.0  # 0→1.0, +100→5.0

    @staticmethod
    def _boost_to_slider(boost):
        """Convert sat_boost back to slider value."""
        if boost <= 1.0:
            return int((boost - 1.0) * 100)  # 0.0→-100, 1.0→0
        return int((boost - 1.0) * 100 / 4.0)  # 1.0→0, 5.0→100

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #2b2b2b; }
            QLabel { color: #ddd; }
            QGroupBox { color: #ddd; border: 1px solid #444; border-radius: 6px;
                        margin-top: 8px; padding-top: 14px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { background: #3a7bd5; color: white; border: none; padding: 8px 16px;
                          border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background: #4a8be5; }
            QPushButton:checked { background: #e74c3c; }
            QSlider::groove:horizontal { height: 6px; background: #444; border-radius: 3px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; background: #3a7bd5;
                                          border-radius: 7px; margin: -4px 0; }
            QTabWidget::pane { border: 1px solid #444; border-radius: 4px; }
            QTabBar::tab { background: #333; color: #aaa; padding: 6px 16px;
                           border-radius: 4px 4px 0 0; }
            QTabBar::tab:selected { background: #444; color: #fff; }
            QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
                background: #333; color: #ddd; border: 1px solid #555;
                padding: 4px; border-radius: 3px; }
        """)

    def _populate_monitors(self):
        with mss_cls() as sct:
            for i, m in enumerate(sct.monitors):
                if i == 0:
                    continue
                self.monitor_combo.addItem(f"Monitor {i}: {m['width']}x{m['height']}", i)
        idx = self.monitor_combo.findData(self.config.sync.monitor_index)
        if idx >= 0:
            self.monitor_combo.setCurrentIndex(idx)
        self.monitor_combo.currentIndexChanged.connect(
            lambda _: setattr(self.config.sync, "monitor_index", self.monitor_combo.currentData())
        )

    def _capture_preview(self):
        """Lightweight preview — just grab, downsample, show. No heavy blur here."""
        try:
            if self._sct is None:
                self._sct = mss_cls()
            mon_idx = self.config.sync.monitor_index
            if mon_idx >= len(self._sct.monitors):
                return
            mon = self._sct.monitors[mon_idx]
            w, h = mon["width"], mon["height"]

            img = self._sct.grab(mon)
            frame = np.frombuffer(img.raw, dtype=np.uint8).reshape(h, w, 4)

            # downsample to thumbnail
            step_y = max(1, h // 90)
            step_x = max(1, w // 160)
            thumb = frame[::step_y, ::step_x, :3][:, :, ::-1].copy()

            # apply blur to thumbnail (cheap — it's ~160x90)
            blur_r = self.config.sync.blur_radius
            if blur_r > 0:
                thumb = apply_blur(thumb, blur_r)

            # crop box
            crop = self.config.sync.crop
            x0_f = crop.left / 100.0
            y0_f = crop.top / 100.0
            x1_f = 1.0 - crop.right / 100.0
            y1_f = 1.0 - crop.bottom / 100.0
            has_crop = x0_f > 0.001 or y0_f > 0.001 or x1_f < 0.999 or y1_f < 0.999
            crop_rect = (x0_f, y0_f, x1_f, y1_f) if has_crop else None

            self.preview.show_frame(thumb, crop_rect)

            # when sync is off, compute the color locally for the info labels
            if not (self.sync_thread and self.sync_thread.isRunning()):
                sh, sw = thumb.shape[:2]
                cy0 = int(sh * y0_f)
                cy1 = max(cy0 + 1, int(sh * y1_f))
                cx0 = int(sw * x0_f)
                cx1 = max(cx0 + 1, int(sw * x1_f))
                cropped = thumb[cy0:cy1, cx0:cx1]
                ch, cw_px = cropped.shape[:2]

                flat = cropped.reshape(-1, 3)
                center_w = make_center_weights(cw_px, ch, 1)
                bcfg = self.config.day if self.mode == "day" else self.config.night
                x, y, bri, preview_rgb = extract_scene_color(
                    flat, center_w, bcfg.min_brightness, bcfg.max_brightness,
                    sat_boost=self.config.sync.saturation,
                    gamma=self.config.sync.gamma,
                )
                # swatch: use xy_to_rgb with correct inverse matrix — matches bulb output
                from .color import xy_to_rgb
                sr, sg, sb = xy_to_rgb(x, y, bri)
                self.swatch.set_color(sr, sg, sb)
                self._update_info_labels(x, y, bri, sr, sg, sb)

        except (OSError, ValueError, ImportError) as e:
            self.status_label.setText(f"Preview error: {type(e).__name__}")

    def _on_sync_color(self, x, y, bri, r, g, b):
        """Called by sync thread — this IS what the bulb is receiving."""
        self.swatch.set_color(r, g, b)
        self._update_info_labels(x, y, bri, r, g, b)

    def _update_info_labels(self, x, y, bri, r, g, b):
        bcfg = self.config.day if self.mode == "day" else self.config.night
        bri_pct = int(100 * bri / bcfg.max_brightness) if bcfg.max_brightness > 0 else 0
        maxc = max(r, g, b, 1)
        minc = min(r, g, b)
        sat_pct = int(100 * (maxc - minc) / maxc)
        if maxc == minc:
            hue_deg = 0
        elif maxc == r:
            hue_deg = int(60 * ((g - b) / (maxc - minc) % 6))
        elif maxc == g:
            hue_deg = int(60 * ((b - r) / (maxc - minc) + 2))
        else:
            hue_deg = int(60 * ((r - g) / (maxc - minc) + 4))

        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self.hue_label.setText(f"hue  {hue_deg}°")
        self.sat_label.setText(f"sat  {sat_pct}%")
        self.bri_label.setText(f"bri  {bri}/{bcfg.max_brightness}  ({bri_pct}%)")
        self.xy_label.setText(f"xy   {x:.3f}, {y:.3f}")
        self.rgb_label.setText(f"rgb  {r},{g},{b}  {hex_color}")

    def _manual_push(self, x, y, bri):
        cfg = self.config.bridge
        if not cfg.ip or not cfg.api_user:
            return
        body = json.dumps({
            "xy": [round(x, 4), round(y, 4)],
            "bri": bri,
            "transitiontime": 4,
        }).encode()
        def _send():
            for light_id in cfg.light_ids:
                url = f"http://{cfg.ip}/api/{cfg.api_user}/lights/{light_id}/state"
                req = urllib.request.Request(url, data=body, method="PUT")
                try:
                    urllib.request.urlopen(req, timeout=0.5)
                except Exception:
                    pass
        threading.Thread(target=_send, daemon=True).start()

    def _toggle_sync(self):
        if self.sync_thread and self.sync_thread.isRunning():
            self.sync_thread.stop()
            self.sync_thread = None
            self.toggle_btn.setText("Start Sync")
            self.toggle_btn.setChecked(False)
            self.status_label.setText("Preview only")
            self.manual_tab.set_enabled_state(False)
            self._update_tray()
        else:
            self.sync_thread = SyncThread(self.config, self.mode)
            self.sync_thread.color_updated.connect(self._on_sync_color)
            self.sync_thread.error_occurred.connect(self._on_error)
            self.sync_thread.start()
            self.toggle_btn.setText("Stop Sync")
            self.toggle_btn.setChecked(True)
            self.status_label.setText(f"Syncing ({self.mode})")
            self.manual_tab.set_enabled_state(True)
            self._update_tray()

    def _cycle_mode(self):
        if self.mode == "night":
            self.mode = "day"
        else:
            self.mode = "night"
        self._update_mode_indicator()
        self._update_tray()
        if self.sync_thread:
            self.sync_thread.set_mode(self.mode)
            self.status_label.setText(f"Syncing ({self.mode})")

    def _update_mode_indicator(self):
        if self.mode == "day":
            self.mode_indicator.setText("DAY MODE ACTIVE")
            self.mode_indicator.setStyleSheet(
                "background: #3d3420; color: #d4a84b; font-size: 10px; letter-spacing: 2px; "
                "border-top: 2px solid #f39c12; padding: 4px;")
        else:
            self.mode_indicator.setText("NIGHT MODE ACTIVE")
            self.mode_indicator.setStyleSheet(
                "background: #1a2332; color: #5a7a94; font-size: 10px; letter-spacing: 2px; "
                "border-top: 2px solid #2c4a6a; padding: 4px;")

    def _on_error(self, msg):
        self.status_label.setText(f"Error: {msg}")

    def _discover_bridge(self):
        self.bridge_status.setText("Searching...")
        self.bridge_status.repaint()
        bridges = discover_bridges()
        if bridges:
            ip = bridges[0].get("internalipaddress", "")
            self.ip_edit.setText(ip)
            self.config.bridge.ip = ip
            self.bridge_status.setText(f"Found: {ip}")
            self._refresh_lights()
        else:
            self.bridge_status.setText("No bridges found on network")

    def _pair_bridge(self):
        ip = self.config.bridge.ip
        if not ip:
            self.bridge_status.setText("Set bridge IP first")
            return
        self.bridge_status.setText("Pairing... press the bridge button!")
        self.bridge_status.repaint()
        user = create_api_user(ip)
        if user:
            self.api_edit.setText(user)
            self.config.bridge.api_user = user
            self.bridge_status.setText("Paired successfully!")
            self._refresh_lights()
        else:
            self.bridge_status.setText("Pairing failed — press the bridge button and retry")

    def _format_light_ids(self):
        ids = self.config.bridge.light_ids
        return f"Active lights: {', '.join(str(i) for i in ids)}" if ids else "No lights selected"

    def _refresh_lights(self):
        ip = self.config.bridge.ip
        user = self.config.bridge.api_user
        if not ip or not user:
            return
        lights = get_color_lights(ip, user)
        # clear old checkboxes
        while self.light_checks_layout.count():
            item = self.light_checks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        active_ids = set(self.config.bridge.light_ids)
        for l in lights:
            cb = QCheckBox(f"{l['name']} (#{l['id']})")
            cb.setChecked(l["id"] in active_ids)
            cb.setStyleSheet("color: #ccc;")
            lid = l["id"]
            cb.toggled.connect(lambda checked, _id=lid: self._toggle_light(_id, checked))
            self.light_checks_layout.addWidget(cb)

        self.light_list.setText(self._format_light_ids())

    def _toggle_light(self, light_id, checked):
        ids = set(self.config.bridge.light_ids)
        if checked:
            ids.add(light_id)
        else:
            ids.discard(light_id)
        self.config.bridge.light_ids = sorted(ids)
        self.light_list.setText(self._format_light_ids())

    def _save(self):
        save_config(self.config)
        self.status_label.setText("Settings saved")
        # flash the save button green->normal as confirmation
        self._save_btn.setText("✓  Saved!")
        self._save_btn.setStyleSheet("background: #1e8449; font-weight: bold;")
        QTimer.singleShot(1500, lambda: (
            self._save_btn.setText("Save Settings"),
            self._save_btn.setStyleSheet("background: #27ae60;"),
        ))

    def changeEvent(self, event):
        if getattr(self, 'tray', None) and event.type() == event.Type.WindowStateChange and self.isMinimized():
            self.hide()
            event.ignore()
            return
        super().changeEvent(event)

    def closeEvent(self, event):
        if getattr(self, 'tray', None):
            self.hide()
            event.ignore()
        else:
            self.quit_app()
            event.accept()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._show_window()

    def _update_tray(self):
        if not self.tray:
            return
        syncing = self.sync_thread and self.sync_thread.isRunning()
        self.tray.setIcon(make_tray_icon(syncing, self.mode))

        sync_str = "on" if syncing else "off"
        mode_str = "Day" if self.mode == "day" else "Night"
        self.tray.setToolTip(f"Hue Screen Sync — Sync {sync_str} | {mode_str}")

        self._tray_status.setText(f"Sync: {sync_str}  |  {mode_str} mode")
        self._tray_sync.setText("Stop Sync" if syncing else "Start Sync")
        other_mode = "Day" if self.mode == "night" else "Night"
        self._tray_mode.setText(f"Switch to {other_mode}")

    def quit_app(self):
        if self.sync_thread and self.sync_thread.isRunning():
            self.sync_thread.stop()
        if self._sct:
            self._sct.close()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Hue Screen Sync")

    config = load_config()
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
