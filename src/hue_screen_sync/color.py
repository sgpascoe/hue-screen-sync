"""Color extraction and CIE xy conversion for Philips Hue.

Uses the Philips SDK Wide RGB D65 matrix and Gamut C clamping.
Averages in OKLab for perceptually correct color blending.
"""

import numpy as np


# --- Philips SDK Wide RGB D65 matrices ---

def rgb_to_xy(r: float, g: float, b: float) -> tuple[float, float]:
    """Convert sRGB [0-1] to CIE xy using Philips SDK coefficients."""
    r = _srgb_to_linear(r)
    g = _srgb_to_linear(g)
    b = _srgb_to_linear(b)

    X = r * 0.664511 + g * 0.154324 + b * 0.162028
    Y = r * 0.283881 + g * 0.668433 + b * 0.047685
    Z = r * 0.000088 + g * 0.072310 + b * 0.986039

    total = X + Y + Z
    if total < 1e-6:
        return 0.3127, 0.3290
    x = X / total
    y = max(Y / total, 1e-6)

    return gamut_clamp(x, y)


def xy_to_rgb(x: float, y: float, bri: int) -> tuple[int, int, int]:
    """Convert CIE xy + Hue brightness to sRGB, using the SAME Wide RGB D65 inverse."""
    Y = bri / 254.0
    if y < 1e-6:
        return 0, 0, 0
    X = (Y / y) * x
    Z = (Y / y) * (1.0 - x - y)

    # Wide RGB D65 inverse (matches the forward matrix)
    r =  X * 1.656723 + Y * -0.354862 + Z * -0.255038
    g = -X * 0.707882 + Y *  1.655235 + Z *  0.036152
    b =  X * 0.051718 + Y * -0.121026 + Z *  1.011953

    # normalize by max to preserve hue when out of gamut
    peak = max(r, g, b)
    if peak > 1.0:
        r, g, b = r / peak, g / peak, b / peak

    def gamma(c):
        c = max(0.0, c)
        if c > 0.0031308:
            return min(1.0, 1.055 * (c ** (1.0 / 2.4)) - 0.055)
        return min(1.0, 12.92 * c)

    return int(gamma(r) * 255), int(gamma(g) * 255), int(gamma(b) * 255)


# --- Gamut C (for LCA001 and most modern Hue color bulbs) ---

GAMUT_C_RED = (0.6915, 0.3083)
GAMUT_C_GREEN = (0.17, 0.7)
GAMUT_C_BLUE = (0.1532, 0.0475)


def _cross_product_2d(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _closest_point_on_line(a, b, p):
    ap = (p[0] - a[0], p[1] - a[1])
    ab = (b[0] - a[0], b[1] - a[1])
    ab2 = ab[0] * ab[0] + ab[1] * ab[1]
    if ab2 < 1e-10:
        return a
    t = max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1]) / ab2))
    return (a[0] + t * ab[0], a[1] + t * ab[1])


def _point_in_triangle(p, r, g, b):
    d1 = _cross_product_2d(p, r, g)
    d2 = _cross_product_2d(p, g, b)
    d3 = _cross_product_2d(p, b, r)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def gamut_clamp(x: float, y: float) -> tuple[float, float]:
    """Clamp xy to the nearest point inside Gamut C triangle."""
    p = (x, y)
    r, g, b = GAMUT_C_RED, GAMUT_C_GREEN, GAMUT_C_BLUE
    if _point_in_triangle(p, r, g, b):
        return x, y

    # find closest point on each edge
    candidates = [
        _closest_point_on_line(r, g, p),
        _closest_point_on_line(g, b, p),
        _closest_point_on_line(b, r, p),
    ]
    best = min(candidates, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)
    return best[0], best[1]


# --- sRGB gamma ---

def _srgb_to_linear(c: float) -> float:
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


# --- OKLab for perceptual averaging ---

def _rgb_to_oklab(r, g, b):
    """sRGB [0-1] to OKLab."""
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2220049624 * g + 0.6896925757 * b
    l_ = l_ ** (1.0 / 3.0) if l_ > 0 else 0
    m_ = m_ ** (1.0 / 3.0) if m_ > 0 else 0
    s_ = s_ ** (1.0 / 3.0) if s_ > 0 else 0
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_val = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_val


def _oklab_to_rgb(L, a, b):
    """OKLab to sRGB [0-1]."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l_ = l_ * l_ * l_
    m_ = m_ * m_ * m_
    s_ = s_ * s_ * s_
    r = +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    b_out = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return max(0, min(1, r)), max(0, min(1, g)), max(0, min(1, b_out))


# --- Gaussian blur ---

def apply_blur(frame_rgb: np.ndarray, radius: int) -> np.ndarray:
    """Gaussian blur on a 2D RGB image. radius=0 is no-op."""
    if radius < 1:
        return frame_rgb
    from scipy.ndimage import gaussian_filter
    sigma = radius / 2.0
    blurred = gaussian_filter(frame_rgb.astype(np.float32), sigma=(sigma, sigma, 0))
    return np.clip(blurred, 0, 255).astype(np.uint8)


# --- Centre weight mask ---

def make_center_weights(width: int, height: int, downsample: int) -> np.ndarray:
    """Precompute a 2D Gaussian centre-weight mask, flattened and downsampled."""
    total = width * height
    rows = np.arange(total) // width
    cols = np.arange(total) % width
    cy, cx = height / 2.0, width / 2.0
    dy = (rows - cy) / cy
    dx = (cols - cx) / cx
    w = np.exp(-2.0 * (dx * dx + dy * dy))
    return w[::downsample].astype(np.float32)


# --- Main extraction ---

def extract_scene_color(
    pixels: np.ndarray,
    center_w: np.ndarray,
    min_bri: int,
    max_bri: int,
    sat_boost: float = 1.8,
    gamma: float = 1.8,
) -> tuple[float, float, int, tuple[int, int, int]]:
    """Extract dominant color from screen pixels.

    Returns (x, y, bri, preview_rgb).
    Averages in OKLab, adjusts saturation in HSV, brightness via gamma curve.
    Saturation and brightness are independent — no coupling.
    """
    rf = pixels[:, 0].astype(np.float32)
    gf = pixels[:, 1].astype(np.float32)
    bf = pixels[:, 2].astype(np.float32)

    maxc = np.maximum(np.maximum(rf, gf), bf)

    n = len(rf)
    cw = center_w[:n] if len(center_w) >= n else np.ones(n, dtype=np.float32)

    # clip top 2% (HUD/UI)
    lum_threshold = np.percentile(maxc, 98)
    keep = maxc <= lum_threshold
    rf, gf, bf, maxc, cw = rf[keep], gf[keep], bf[keep], maxc[keep], cw[keep]

    w = cw
    w_sum = w.sum()
    if w_sum < 1e-6:
        w_sum = 1.0

    # average in OKLab for perceptually correct blending
    r_norm = rf / 255.0
    g_norm = gf / 255.0
    b_norm = bf / 255.0

    # vectorized OKLab conversion
    r_lin = np.where(r_norm > 0.04045, ((r_norm + 0.055) / 1.055) ** 2.4, r_norm / 12.92)
    g_lin = np.where(g_norm > 0.04045, ((g_norm + 0.055) / 1.055) ** 2.4, g_norm / 12.92)
    b_lin = np.where(b_norm > 0.04045, ((b_norm + 0.055) / 1.055) ** 2.4, b_norm / 12.92)

    l_ = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
    m_ = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
    s_ = 0.0883024619 * r_lin + 0.2220049624 * g_lin + 0.6896925757 * b_lin
    l_ = np.cbrt(np.maximum(l_, 0))
    m_ = np.cbrt(np.maximum(m_, 0))
    s_ = np.cbrt(np.maximum(s_, 0))
    ok_L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    ok_a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    ok_b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

    # weighted average in OKLab
    avg_L = (ok_L * w).sum() / w_sum
    avg_a = (ok_a * w).sum() / w_sum
    avg_b = (ok_b * w).sum() / w_sum

    # convert back to sRGB
    r_avg, g_avg, b_avg = _oklab_to_rgb(avg_L, avg_a, avg_b)

    # --- saturation boost in HSV (independent of brightness) ---
    hsv_max = max(r_avg, g_avg, b_avg, 1e-6)
    hsv_min = min(r_avg, g_avg, b_avg)
    hsv_v = hsv_max
    hsv_s = (hsv_max - hsv_min) / hsv_max
    # compute hue
    if hsv_max == hsv_min:
        hsv_h = 0.0
    elif hsv_max == r_avg:
        hsv_h = ((g_avg - b_avg) / (hsv_max - hsv_min)) % 6.0
    elif hsv_max == g_avg:
        hsv_h = (b_avg - r_avg) / (hsv_max - hsv_min) + 2.0
    else:
        hsv_h = (r_avg - g_avg) / (hsv_max - hsv_min) + 4.0
    hsv_h /= 6.0

    # apply saturation gain with soft-clip curve:
    # approaches 1.0 asymptotically, never hard-clamps — preserves golden/amber tones
    # at boost=1: identity. at boost=5, S=0.6 → 0.88 (not 1.0)
    if sat_boost <= 0:
        hsv_s = 0.0
    elif hsv_s > 0:
        hsv_s = (hsv_s * sat_boost) / (1.0 + hsv_s * (sat_boost - 1.0))
    hsv_s = min(hsv_s, 1.0)

    # reconstruct RGB from HSV at full value (for xy extraction)
    h6 = hsv_h * 6.0
    i = int(h6)
    f = h6 - i
    p = 1.0 - hsv_s
    q = 1.0 - hsv_s * f
    t = 1.0 - hsv_s * (1.0 - f)
    if i % 6 == 0:
        r_out, g_out, b_out = 1.0, t, p
    elif i % 6 == 1:
        r_out, g_out, b_out = q, 1.0, p
    elif i % 6 == 2:
        r_out, g_out, b_out = p, 1.0, t
    elif i % 6 == 3:
        r_out, g_out, b_out = p, q, 1.0
    elif i % 6 == 4:
        r_out, g_out, b_out = t, p, 1.0
    else:
        r_out, g_out, b_out = 1.0, p, q

    # xy from the saturated color at full brightness
    x, y = rgb_to_xy(r_out, g_out, b_out)

    # --- brightness via gamma curve (independent of saturation) ---
    scene_lum = avg_L  # OKLab L is perceptual lightness 0-1
    bri = max(min_bri, min(max_bri, int(max_bri * (scene_lum ** gamma))))

    preview_rgb = (int(r_out * 255), int(g_out * 255), int(b_out * 255))
    return x, y, bri, preview_rgb
