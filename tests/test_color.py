"""Tests for the color pipeline — gamut clamping, OKLab round-trip, conversions."""

import numpy as np
import pytest
from hue_screen_sync.color import (
    rgb_to_xy, xy_to_rgb, gamut_clamp,
    extract_scene_color, make_center_weights,
    GAMUT_C_RED, GAMUT_C_GREEN, GAMUT_C_BLUE,
    _point_in_triangle,
)


class TestGamutClamp:
    def test_point_inside_gamut_unchanged(self):
        x, y = gamut_clamp(0.35, 0.35)
        assert abs(x - 0.35) < 0.001
        assert abs(y - 0.35) < 0.001

    def test_point_outside_gamut_clamped(self):
        x, y = gamut_clamp(0.8, 0.1)
        assert _point_in_triangle((x, y), GAMUT_C_RED, GAMUT_C_GREEN, GAMUT_C_BLUE)

    def test_white_point_inside(self):
        assert _point_in_triangle((0.3127, 0.3290), GAMUT_C_RED, GAMUT_C_GREEN, GAMUT_C_BLUE)


class TestRgbToXy:
    def test_red_clamps_to_gamut_c_red(self):
        x, y = rgb_to_xy(1, 0, 0)
        assert abs(x - GAMUT_C_RED[0]) < 0.01
        assert abs(y - GAMUT_C_RED[1]) < 0.01

    def test_green_clamps_to_gamut_c_green(self):
        x, y = rgb_to_xy(0, 1, 0)
        assert abs(x - GAMUT_C_GREEN[0]) < 0.01
        assert abs(y - GAMUT_C_GREEN[1]) < 0.01

    def test_blue_clamps_to_gamut_c_blue(self):
        x, y = rgb_to_xy(0, 0, 1)
        assert abs(x - GAMUT_C_BLUE[0]) < 0.01
        assert abs(y - GAMUT_C_BLUE[1]) < 0.01

    def test_white_near_d65(self):
        x, y = rgb_to_xy(1, 1, 1)
        assert abs(x - 0.3127) < 0.02
        assert abs(y - 0.3290) < 0.02

    def test_black_returns_d65(self):
        x, y = rgb_to_xy(0, 0, 0)
        assert abs(x - 0.3127) < 0.01
        assert abs(y - 0.3290) < 0.01


class TestXyToRgb:
    def test_high_bri_white_is_bright(self):
        r, g, b = xy_to_rgb(0.3127, 0.3290, 254)
        assert r > 200 and g > 200 and b > 200

    def test_low_bri_is_dim(self):
        r, g, b = xy_to_rgb(0.3127, 0.3290, 10)
        assert r < 100 and g < 100 and b < 100

    def test_zero_bri_is_black(self):
        r, g, b = xy_to_rgb(0.3127, 0.3290, 0)
        assert r == 0 and g == 0 and b == 0

    def test_red_xy_produces_red_dominant(self):
        r, g, b = xy_to_rgb(0.6915, 0.3083, 200)
        assert r > g and r > b

    def test_hue_preserved_on_out_of_gamut(self):
        r1, g1, b1 = xy_to_rgb(0.6, 0.35, 254)
        r2, g2, b2 = xy_to_rgb(0.6, 0.35, 100)
        if r1 > 0 and r2 > 0:
            ratio1 = g1 / max(r1, 1)
            ratio2 = g2 / max(r2, 1)
            assert abs(ratio1 - ratio2) < 0.15


class TestExtractSceneColor:
    def test_uniform_red_scene(self):
        pixels = np.full((100, 3), [200, 30, 10], dtype=np.uint8)
        cw = make_center_weights(10, 10, 1)
        x, y, bri, rgb = extract_scene_color(pixels, cw, 5, 200)
        assert x > 0.5  # warm/red region

    def test_uniform_blue_scene(self):
        pixels = np.full((100, 3), [20, 40, 180], dtype=np.uint8)
        cw = make_center_weights(10, 10, 1)
        x, y, bri, rgb = extract_scene_color(pixels, cw, 5, 200)
        assert x < 0.25  # blue region

    def test_dark_scene_respects_min_bri(self):
        pixels = np.full((100, 3), [5, 5, 5], dtype=np.uint8)
        cw = make_center_weights(10, 10, 1)
        x, y, bri, rgb = extract_scene_color(pixels, cw, 20, 200)
        assert bri >= 20

    def test_bright_scene_respects_max_bri(self):
        pixels = np.full((100, 3), [250, 250, 250], dtype=np.uint8)
        cw = make_center_weights(10, 10, 1)
        x, y, bri, rgb = extract_scene_color(pixels, cw, 5, 100)
        assert bri <= 100

    def test_sat_boost_zero_near_white(self):
        pixels = np.full((100, 3), [100, 80, 60], dtype=np.uint8)
        cw = make_center_weights(10, 10, 1)
        x, y, bri, rgb = extract_scene_color(pixels, cw, 5, 200, sat_boost=0.0)
        assert abs(x - 0.3127) < 0.05  # near white point


class TestSliderInverse:
    """Verify _slider_to_boost and _boost_to_slider are inverse functions."""

    def test_round_trip_positive(self):
        from hue_screen_sync.app import MainWindow
        for v in [0, 10, 25, 50, 75, 100]:
            boost = MainWindow._slider_to_boost(v)
            back = MainWindow._boost_to_slider(boost)
            assert abs(back - v) <= 1, f"slider={v} -> boost={boost} -> back={back}"

    def test_round_trip_negative(self):
        from hue_screen_sync.app import MainWindow
        for v in [-100, -75, -50, -25, 0]:
            boost = MainWindow._slider_to_boost(v)
            back = MainWindow._boost_to_slider(boost)
            assert abs(back - v) <= 1, f"slider={v} -> boost={boost} -> back={back}"

    def test_zero_is_neutral(self):
        from hue_screen_sync.app import MainWindow
        assert MainWindow._slider_to_boost(0) == 1.0

    def test_minus_100_is_zero(self):
        from hue_screen_sync.app import MainWindow
        assert MainWindow._slider_to_boost(-100) == 0.0
