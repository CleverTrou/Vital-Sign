#!/usr/bin/env python3
"""Regenerate every icon file from the sources in assets/icon/.

Sources (edit these, then rerun):
  assets/icon/src/icon-macos-1024.svg    classic macOS icon (plate, shadow)
  assets/icon/src/icon-windows-1024.svg  Windows icon, 32 px and up
  assets/icon/src/icon-small-32.svg      simplified art for 16-24 px
  assets/icon/VitalSign.icon/            layered Icon Composer document for
                                         macOS 26+ (Default/Dark/Clear/Tinted)

Outputs (committed, so nobody needs these tools just to run the app):
  assets/icon/vitalsign.icns             classic macOS icon
  assets/icon/vitalsign.ico              Windows, 16-256 px
  assets/icon/vitalsign-{256,512}.png    window/Dock icon for Tk
  assets/icon/ui/*.png                   30 px header icon and readout hearts
                                         (normal and dimmed) for the control
                                         window
  assets/icon/appearances.png            preview of the macOS 26+ variants
  <app>/Contents/Resources/{vitalsign.icns,Assets.car} for both app bundles

macOS only. Needs rsvg-convert and ImageMagick (Homebrew: librsvg,
imagemagick) and Xcode 26 or newer for actool/Icon Composer's ictool.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "icon"
SRC = ICON / "src"
DOCUMENT = ICON / "VitalSign.icon"
BUNDLES = [ROOT / "Vital Sign.app", ROOT / "VitalSignBridge.app"]
ICTOOL = Path("/Applications/Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool")
APPEARANCES = ["Default", "Dark", "ClearLight", "ClearDark", "TintedLight", "TintedDark"]


def run(*cmd: str | Path) -> None:
    subprocess.run([str(c) for c in cmd], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def render(svg: Path, size: int, out: Path) -> Path:
    run("rsvg-convert", "-w", size, "-h", size, "-o", out, svg)
    return out


def build_icns(tmp: Path) -> None:
    iconset = tmp / "vitalsign.iconset"
    iconset.mkdir()
    master = SRC / "icon-macos-1024.svg"
    for size in (16, 32, 128, 256, 512):
        render(master, size, iconset / f"icon_{size}x{size}.png")
        render(master, size * 2, iconset / f"icon_{size}x{size}@2x.png")
    run("iconutil", "-c", "icns", "-o", ICON / "vitalsign.icns", iconset)


def build_ico(tmp: Path) -> None:
    pngs = [render(SRC / "icon-small-32.svg", s, tmp / f"ico-{s}.png") for s in (16, 20, 24)]
    pngs += [render(SRC / "icon-windows-1024.svg", s, tmp / f"ico-{s}.png") for s in (32, 40, 48, 64, 256)]
    run("magick", *pngs, ICON / "vitalsign.ico")


def build_pngs() -> None:
    for size in (256, 512):
        render(SRC / "icon-windows-1024.svg", size, ICON / f"vitalsign-{size}.png")


def build_ui_pngs(tmp: Path) -> None:
    """Small antialiased images for the control window.

    Tk rounds canvas coordinates to whole points, so curves drawn as
    polygons come out stair-stepped on Retina screens; pre-rendered images
    stay smooth. The dimmed heart is pre-blended because Tk images have no
    opacity (see vitalsign.brand.STALE_OPACITY).
    """
    sys.path.insert(0, str(ROOT))
    from vitalsign import brand

    ui = ICON / "ui"
    ui.mkdir(exist_ok=True)
    # The macOS master cropped to its plate, so the icon fills its 30 px box.
    plate = tmp / "plate.svg"
    plate.write_text(
        (SRC / "icon-macos-1024.svg").read_text().replace('viewBox="0 0 1024 1024"', 'viewBox="100 100 824 824"', 1)
    )
    render(plate, 30, ui / "header-icon.png")
    heart = (SRC / "heart.svg").read_text()
    for name, color in (("heart.png", brand.PULSE), ("heart-stale.png", brand.blend(brand.PULSE, brand.PANEL, brand.STALE_OPACITY))):
        svg = tmp / name.replace(".png", ".svg")
        svg.write_text(heart.replace('fill="#ff4d6d"', f'fill="{color}"'))
        render(svg, 30, ui / name)


def build_asset_catalog(tmp: Path) -> Path:
    out = tmp / "actool"
    out.mkdir()
    run(
        "xcrun", "actool", DOCUMENT,
        "--compile", out,
        "--platform", "macosx",
        "--minimum-deployment-target", "11.0",
        "--app-icon", "VitalSign",
        "--output-partial-info-plist", out / "partial.plist",
        "--target-device", "mac",
    )
    return out / "Assets.car"


def build_preview(tmp: Path) -> None:
    renders = []
    for rendition in APPEARANCES:
        out = tmp / f"{rendition}.png"
        run(
            ICTOOL, DOCUMENT, "--export-image", "--output-file", out, "--platform", "macOS",
            "--rendition", rendition, "--width", "256", "--height", "256", "--scale", "1",
        )
        renders.append(out)
    run("magick", *renders, "-background", "none", "-gravity", "center", "-splice", "16x0", "+append", "-chop", "16x0", "-depth", "8", "-strip", ICON / "appearances.png")


def install(assets_car: Path) -> None:
    for bundle in BUNDLES:
        resources = bundle / "Contents" / "Resources"
        resources.mkdir(exist_ok=True)
        shutil.copy2(ICON / "vitalsign.icns", resources / "vitalsign.icns")
        shutil.copy2(assets_car, resources / "Assets.car")


def main() -> int:
    if sys.platform != "darwin":
        print("build_icons.py needs macOS (iconutil, actool, ictool).", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        build_icns(tmp)
        build_ico(tmp)
        build_pngs()
        build_ui_pngs(tmp)
        install(build_asset_catalog(tmp))
        build_preview(tmp)
    print("Icons rebuilt. Commit assets/icon/ and both app bundles' Resources/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
