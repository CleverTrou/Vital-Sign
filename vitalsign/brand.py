"""Brand tokens, the bundled typeface, and vector shapes for the control app.

Colors and type come from the design handoff (README "Brand"); the heart
is the same path overlay.html draws, so the app and the overlay match.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
ICON_DIR = ROOT / "assets" / "icon"
ICON_PNG = ICON_DIR / "vitalsign-256.png"
ICON_ICO = ICON_DIR / "vitalsign.ico"

BRAND_FAMILY = "Atkinson Hyperlegible"
FONT_FILES = ("AtkinsonHyperlegible-Regular.ttf", "AtkinsonHyperlegible-Bold.ttf")
MONO_FAMILY = "Consolas" if sys.platform == "win32" else "Menlo"

# Tokens. Pulse marks heart rate and the brand; the SpO2 colors only ever
# mean severity, so the brand never reads as a warning.
PULSE = "#ff4d6d"
PANEL = "#12142a"
INK = "#f5f5f7"
INK_MUTED = "#b1b2b9"  # ink at 70% over the panel
DIVIDER = "#41434f"  # white at 20% over the panel
TREND_AREA = "#30182f"  # pulse at 12% over the panel
SPO2_GOOD = "#4ade80"
SPO2_WARN = "#facc15"
SPO2_BAD = "#f87171"
RUNNING = "#2fa04f"
STARTING = "#c98a00"
LOG_WARNING = "#c98a00"
LOG_ERROR = "#dd3333"
MIDNIGHT = "#1b1f45"  # flat stand-in for the icon plate's gradient at small sizes

STALE_OPACITY = 0.35  # overlay.html's .panel.stale


def spo2_severity(spo2: int) -> tuple[str, str]:
    """(color, marker) -- the same thresholds as overlay.html's spo2Severity()."""
    if spo2 >= 95:
        return SPO2_GOOD, ""
    if spo2 >= 90:
        return SPO2_WARN, "▲"
    return SPO2_BAD, "⚠︎"  # U+FE0E: the plain-text glyph, not the color emoji


def blend(color: str, onto: str, opacity: float) -> str:
    """``color`` at ``opacity`` over ``onto``. Tk canvases have no alpha."""
    c = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(onto[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(bb + (cc - bb) * opacity):02x}" for cc, bb in zip(c, b))


# overlay.html's heart (viewBox 0 0 24 24), as absolute cubic Béziers:
# M12 21s-7.5-4.6-10-9.1C.3 8.6 1.7 4.9 5.2 4.1 7.5 3.6 9.8 4.6 12 7
# c2.2-2.4 4.5-3.4 6.8-2.9 3.5.8 4.9 4.5 3.2 7.8-2.5 4.5-10 9.1-10 9.1z
_HEART_START = (12.0, 21.0)
_HEART_CURVES = (
    ((12.0, 21.0), (4.5, 16.4), (2.0, 11.9)),
    ((0.3, 8.6), (1.7, 4.9), (5.2, 4.1)),
    ((7.5, 3.6), (9.8, 4.6), (12.0, 7.0)),
    ((14.2, 4.6), (16.5, 3.6), (18.8, 4.1)),
    ((22.3, 4.9), (23.7, 8.6), (22.0, 11.9)),
    ((19.5, 16.4), (12.0, 21.0), (12.0, 21.0)),
)


def heart_points(x: float, y: float, size: float, steps: int = 12) -> list[float]:
    """Polygon points for the heart, its 24-unit box scaled to ``size``."""
    s = size / 24
    pts = [x + _HEART_START[0] * s, y + _HEART_START[1] * s]
    p0 = _HEART_START
    for c1, c2, p3 in _HEART_CURVES:
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            px = u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p3[0]
            py = u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p3[1]
            pts += [x + px * s, y + py * s]
        p0 = p3
    return pts


def gauge_points(cx: float, cy: float, r: float, steps: int = 48) -> list[float]:
    """The icon's gauge arc: about 94% of a circle, gap at the top."""
    # The SVG starts the dash at -79.2 deg (from 3 o'clock, clockwise in
    # screen space) and draws 1654/1760 of the circumference.
    start = math.radians(-79.2)
    sweep = 2 * math.pi * 1654 / 1760
    pts = []
    for i in range(steps + 1):
        a = start + sweep * i / steps
        pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 6) -> list[float]:
    """Polygon for a rounded rectangle (Tk has no native one)."""
    pts = []
    corners = ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180))
    for cx, cy, start in corners:
        for i in range(steps + 1):
            a = math.radians(start + 90 * i / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def register_brand_font() -> bool:
    """Make the bundled Atkinson Hyperlegible visible to Tk, for this process only.

    Nothing is installed system-wide. Call before creating tk.Tk(). Returns
    False (and the app falls back to the system font) if the platform call
    is unavailable or fails.
    """
    paths = [FONT_DIR / f for f in FONT_FILES if (FONT_DIR / f).exists()]
    if not paths:
        return False
    try:
        if sys.platform == "darwin":
            return _register_macos(paths)
        if sys.platform == "win32":
            return _register_windows(paths)
    except (OSError, AttributeError):
        return False
    return False  # Linux: fontconfig would need the files in a font dir


def _register_macos(paths: list[Path]) -> bool:
    import ctypes

    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreText.framework/CoreText")
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    process_scope = 1  # kCTFontManagerScopeProcess
    ok = True
    for path in paths:
        raw = str(path).encode("utf-8")
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
        if not url:
            return False
        try:
            ok = ct.CTFontManagerRegisterFontsForURL(url, process_scope, None) and ok
        finally:
            cf.CFRelease(url)
    return ok


def _register_windows(paths: list[Path]) -> bool:
    import ctypes

    fr_private = 0x10  # visible to this process only; nothing is installed
    gdi32 = ctypes.windll.gdi32
    return all(gdi32.AddFontResourceExW(str(path), fr_private, 0) > 0 for path in paths)
