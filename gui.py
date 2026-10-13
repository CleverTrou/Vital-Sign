#!/usr/bin/env python3
"""Vital Sign control app: start/stop the bridge and choose its sources.

A small window around the same pieces OBS automation uses: it edits
config.sh / config.ps1 and runs start_bridge / stop_bridge, so the app
and Advanced Scene Switcher always agree on what's running. Live
readings come from the bridge's own WebSocket, like the overlay's.

Closing the window leaves the bridge running (your stream shouldn't
lose its overlay because a settings window closed); use Stop for that.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

import websockets

from vitalsign import brand
from vitalsign.config_file import config_path, read_settings, write_sources
from vitalsign.protocols import HDS_DEFAULT_PORT
from vitalsign.sources import parse_source

ROOT = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
CONFIG = config_path(ROOT, WINDOWS)
PIDFILE = ROOT / ".bridge.pid"
LOGFILE = ROOT / "bridge.log"
# Device names from scans, keyed by source spec, so the list can say
# "O2M 3994" instead of a 36-character CoreBluetooth UUID.
NAMES_FILE = ROOT / ".gui-device-names.json"

# Hide the console window PowerShell/python would otherwise flash up.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

STATE_TEXT = {
    "connected": "Connected",
    "connecting": "Connecting…",
    "waiting": "Waiting for device…",
    "idle": "Reconnecting…",
    "disconnected": "Disconnected",
}

TREND_WINDOW_S = 5 * 60  # overlay.html's TREND_WINDOW_MS
SPO2_HIDE_S = 30  # overlay.html's SPO2_HIDE_MS
STALE_S = 8  # overlay.html's STALE_MS
EMPTY_SOURCES = "(none: the bridge scans for one supported Bluetooth device)"
SOURCE_COLUMN_PX = 200
# The platform's own text color (follows light/dark mode on macOS).
TEXT_COLOR = {"darwin": "systemTextColor", "win32": "SystemWindowText"}.get(sys.platform, "black")
STATUS_COLOR = {"darwin": "systemLabelColor", "win32": "SystemWindowText"}.get(sys.platform, "black")
# Tk drops the alpha of macOS's secondary label color, so a fixed gray
# that reads on both light and dark windows stands in for it.
STATUS_SECONDARY = "#8e8e93"

# bridge.log lines: "2026-10-11 21:20:58,376 INFO message"
_LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+) (.*)$")


def lan_ip() -> str | None:
    """This machine's LAN address, for the HDS watch app's server setting.

    connect() on a UDP socket sends nothing; it only makes the OS pick the
    outgoing interface, whose address is the one the watch can reach.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return None


def run_script(name: str) -> subprocess.CompletedProcess:
    if WINDOWS:
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / f"{name}.ps1")]
    else:
        cmd = [str(ROOT / f"{name}.sh")]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)


def short_log_line(line: str) -> tuple[str, str | None]:
    """'HH:MM:SS message' plus a color for warnings/errors (None = normal)."""
    m = _LOG_LINE.match(line)
    if not m:
        return line, None
    clock, level, message = m.groups()
    if level == "WARNING":
        return f"{clock} WARNING {message}", brand.LOG_WARNING
    if level in ("ERROR", "CRITICAL"):
        return f"{clock} {level} {message}", brand.LOG_ERROR
    return f"{clock} {message}", None


def fit_text(text: str, font: tkfont.Font, width: float) -> str:
    """Cut ``text`` to ``width`` pixels with an ellipsis (Tk only clips)."""
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…" if text else ""


def smooth_shape(canvas: tk.Canvas, points: list[float], color: str) -> None:
    """A filled shape with an antialiased edge.

    Tk on macOS draws polygons (fill and outline) without antialiasing, but
    lines with it, so the edge is traced with a thin line over the fill.
    """
    canvas.create_polygon(points, fill=color, outline="")
    canvas.create_line(*points, *points[:2], fill=color, width=1.25, joinstyle="round")


class Fonts:
    """Named fonts, defined once. Atkinson Hyperlegible when it registered,
    the system font otherwise (design variant 3c vs 3b)."""

    def __init__(self, root: tk.Misc, brand_font: bool) -> None:
        family = brand.BRAND_FAMILY if brand_font else tkfont.nametofont("TkDefaultFont").actual("family")
        self.brand_font = brand_font
        if brand_font:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family=family)
        # Negative sizes are pixels (= points on macOS), matching the mock.
        self.wordmark = tkfont.Font(root, family=family, size=-18 if brand_font else -17, weight="bold")
        self.status = tkfont.Font(root, family=family, size=-13, weight="bold")
        self.heading = tkfont.Font(root, family=family, size=-13, weight="bold")
        self.small = tkfont.Font(root, family=family, size=-12)
        self.value = tkfont.Font(root, family=family, size=-44, weight="bold")
        self.unit = tkfont.Font(root, family=family, size=-15)
        self.marker = tkfont.Font(root, family=family, size=-20, weight="bold")
        self.mono = tkfont.Font(root, family=brand.MONO_FAMILY, size=-11)
        self.mono_log = tkfont.Font(root, family=brand.MONO_FAMILY, size=-10)


class Readout(tk.Canvas):
    """The overlay's panel, as OBS shows it, plus status lines beside it.

    The panel is sized to its numbers like overlay.html's (whose
    proportions are scaled here from its 56 px values to 44 px): heart and
    BPM, a divider, SpO2, and the trend underneath, exactly as wide as that
    row. The connection status sits outside the panel, on the window.
    """

    SCALE = 44 / 56  # overlay.html's .value is 56 px
    PAD_X, PAD_Y = round(32 * SCALE), round(18 * SCALE)
    GAP = round(10 * SCALE)  # inside a metric, and between the row and the trend
    METRIC_GAP = round(28 * SCALE)  # around the divider
    DIVIDER_H = round(48 * SCALE)
    TREND_H = round(44 * SCALE)
    RADIUS = round(20 * SCALE)
    ROW_H = 44  # .value has line-height: 1
    HEART = 30
    STATUS_GAP = 16

    def __init__(self, parent: tk.Misc, fonts: Fonts, bg: str) -> None:
        self.fonts = fonts
        # Pre-rendered: polygons come out stair-stepped on Retina in Tk.
        self.heart_images = {
            stale: tk.PhotoImage(master=parent, file=str(brand.UI_DIR / name))
            for stale, name in ((False, "heart.png"), (True, "heart-stale.png"))
        }
        height = self.PAD_Y + self.ROW_H + self.GAP + self.TREND_H + self.PAD_Y
        # 552 wide sets the window's natural width (600 with its padding).
        super().__init__(parent, width=552, height=height, highlightthickness=0, bd=0, bg=bg)
        self.hr: int | None = None
        self.spo2: int | None = None
        self.state_line = "Bridge not running"
        self.source_line = ""
        self.stale = True
        # Hidden for heart-rate-only sources; App decides (SPO2_HIDE_S).
        self.show_spo2 = True
        self.history: list[tuple[float, int]] = []
        self.panel_width = 0.0
        self.trend_box = (0.0, 0.0, 0.0, 0.0)
        # Fixed-width slots for the two metrics, wide enough for any value,
        # so the panel doesn't change width with the digits: Atkinson's
        # figures are proportional ("1" is narrower than "8").
        widest = str(max(range(10), key=lambda d: fonts.value.measure(str(d))))
        marker = max(fonts.marker.measure(m) for m in ("▲", "⚠\ufe0e"))
        self.hr_group = (
            self.HEART + self.GAP + fonts.value.measure(widest * 3) + self.GAP + fonts.unit.measure("bpm")
        )
        self.spo2_group = (
            max(fonts.value.measure("100%"), fonts.value.measure(widest * 2 + "%") + 3 + marker)
            + self.GAP
            + fonts.unit.measure("SpO₂")
        )
        self.bind("<Configure>", lambda _e: self.redraw())

    def color(self, c: str) -> str:
        return brand.blend(c, brand.PANEL, brand.STALE_OPACITY) if self.stale else c

    def record(self, hr: int) -> None:
        now = time.monotonic()
        self.history.append((now, hr))
        self.prune(now)

    def prune(self, now: float) -> None:
        cutoff = now - TREND_WINDOW_S
        while self.history and self.history[0][0] < cutoff:
            self.history.pop(0)

    def redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 1)
        h = int(self.cget("height"))
        f = self.fonts
        top = self.PAD_Y
        # Baseline of a line-height: 1 box, as in the overlay's CSS.
        ascent, descent = f.value.metrics("ascent"), f.value.metrics("descent")
        baseline = top + (self.ROW_H - (ascent + descent)) / 2 + ascent
        mid = top + self.ROW_H / 2

        # Lay the row out first: the panel is sized to it. Each metric gets a
        # fixed-width slot (see __init__) with its content centered in it, so
        # the panel and the divider never move as the digits change.
        items: list = []
        hr_text = str(self.hr) if self.hr else "--"
        hr_w = self.HEART + self.GAP + f.value.measure(hr_text) + self.GAP + f.unit.measure("bpm")
        x = self.PAD_X + (self.hr_group - hr_w) / 2
        items.append(("heart", x))
        x += self.HEART + self.GAP
        items.append(("text", x, hr_text, f.value, brand.INK))
        x += f.value.measure(hr_text) + self.GAP
        items.append(("text", x, "bpm", f.unit, brand.INK_MUTED))

        row_right = self.PAD_X + self.hr_group
        if self.show_spo2:
            divider_x = row_right + self.METRIC_GAP
            items.append(("divider", divider_x))
            spo2_left = divider_x + 1 + self.METRIC_GAP

            marker, spo2_color, spo2_text = "", brand.INK, "--"
            if self.spo2:
                spo2_color, marker = brand.spo2_severity(self.spo2)
                spo2_text = f"{self.spo2}%"
            marker_w = 3 + f.marker.measure(marker) if marker else 0
            spo2_w = f.value.measure(spo2_text) + marker_w + self.GAP + f.unit.measure("SpO₂")
            x = spo2_left + (self.spo2_group - spo2_w) / 2
            items.append(("text", x, spo2_text, f.value, spo2_color))
            x += f.value.measure(spo2_text)
            if marker:
                items.append(("marker", x + 3, marker, spo2_color))
                x += marker_w
            items.append(("text", x + self.GAP, "SpO₂", f.unit, brand.INK_MUTED))
            row_right = spo2_left + self.spo2_group
        self.panel_width = row_right + self.PAD_X

        smooth_shape(self, brand.rounded_rect_points(0.5, 0.5, self.panel_width - 0.5, h - 0.5, self.RADIUS), brand.PANEL)
        for item in items:
            kind = item[0]
            if kind == "heart":
                # The overlay aligns the icon's box to the numbers' baseline.
                self.create_image(item[1], baseline + 3, image=self.heart_images[self.stale], anchor="sw")
            elif kind == "text":
                _kind, ix, text, font, color = item
                self._text(ix, baseline, text, font, color)
            elif kind == "divider":
                self.create_line(item[1], mid - self.DIVIDER_H / 2, item[1], mid + self.DIVIDER_H / 2, fill=self.color(brand.DIVIDER))
            elif kind == "marker":
                _kind, ix, marker, color = item
                self.create_text(ix, baseline - ascent + 4, text=marker, font=f.marker, fill=self.color(color), anchor="nw")

        self.trend_box = (self.PAD_X, top + self.ROW_H + self.GAP, row_right - self.PAD_X, self.TREND_H)
        self._draw_trend(*self.trend_box)

        # Status beside the panel, cut to the room that's left.
        line_h = f.small.metrics("linespace")
        sx = self.panel_width + self.STATUS_GAP
        room = max(w - sx, 0)
        y = h / 2 - (2 * line_h + 2) / 2
        for i, (text, color) in enumerate(((self.state_line, STATUS_COLOR), (self.source_line, STATUS_SECONDARY))):
            self.create_text(sx, y + i * (line_h + 2), text=fit_text(text, f.small, room), font=f.small, fill=color, anchor="nw")

    def _text(self, x: float, baseline: float, text: str, font: tkfont.Font, color: str) -> float:
        """Draw ``text`` on ``baseline``; return the x just after it."""
        self.create_text(x, baseline - font.metrics("ascent"), text=text, font=font, fill=self.color(color), anchor="nw")
        return x + font.measure(text)

    def _draw_trend(self, x0: float, y0: float, w: float, h: float) -> None:
        """overlay.html's drawTrend(): last 5 minutes, scaled to its own min/max."""
        now = time.monotonic()
        self.prune(now)
        if len(self.history) < 2:
            return
        pad = 4
        # Span the oldest reading to now, so a young trend fills the width
        # instead of starting squeezed against the right edge; it settles
        # into a rolling 5-minute window once it has that much history.
        oldest = self.history[0][0]
        window = max(now - oldest, 1.0)
        hrs = [hr for _t, hr in self.history]
        lo, span = min(hrs), max(max(hrs) - min(hrs), 1)
        pts = []
        for t, hr in self.history:
            pts += [
                x0 + pad + (t - oldest) / window * (w - 2 * pad),
                y0 + pad + (1 - (hr - lo) / span) * (h - 2 * pad),
            ]
        area = [pts[0], y0 + h, *pts, pts[-2], y0 + h]
        self.create_polygon(area, fill=self.color(brand.TREND_AREA), outline="")
        self.create_line(pts, fill=self.color(brand.PULSE), width=2, capstyle="round", joinstyle="round")
        dx, dy, r = pts[-2], pts[-1], 3.5
        self.create_oval(dx - r, dy - r, dx + r, dy + r, fill=self.color(brand.PULSE), outline=brand.PANEL, width=2)


class App:
    def __init__(self, root: tk.Tk, brand_font: bool = False) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.settings = read_settings(CONFIG)
        self.names: dict[str, str] = self._load_names()
        self.bridge_up = False
        self.busy = False
        self.last_reading = 0.0
        # When an SpO2 value last arrived (monotonic seconds). Counted from
        # startup, so the full panel shows for the first SPO2_HIDE_S too.
        self.last_spo2 = time.monotonic()
        self.last_state = ""
        self._last_trend_draw = 0.0
        self.fonts = Fonts(root, brand_font)

        root.title("Vital Sign")
        root.minsize(560, 470)
        self._set_window_icon()
        self._build()
        self._refresh_sources()
        LiveFeed(self.settings.ws_port, self.events).start()
        self._poll()

    # -- layout -------------------------------------------------------

    def _set_window_icon(self) -> None:
        try:
            self._icon_image = tk.PhotoImage(file=str(brand.ICON_PNG))
            self.root.iconphoto(True, self._icon_image)
            if WINDOWS:
                self.root.iconbitmap(default=str(brand.ICON_ICO))
        except tk.TclError:
            pass  # an icon is nice to have, never a reason not to open

    def _build(self) -> None:
        style = ttk.Style(self.root)
        window_bg = style.lookup("TFrame", "background") or self.root.cget("background")
        style.configure("Status.TLabel", font=self.fonts.status)
        style.configure("Wordmark.TLabel", font=self.fonts.wordmark)
        style.configure("Heading.TLabel", font=self.fonts.heading)
        style.configure("Muted.TLabel", font=self.fonts.small, foreground="gray")
        style.configure("Log.TLabel", font=self.fonts.mono_log, foreground="gray")

        outer = ttk.Frame(self.root, padding=(24, 18, 24, 12))
        outer.pack(fill="both", expand=True)

        # Header: icon, wordmark, status, then Restart and Start/Stop.
        header = ttk.Frame(outer)
        header.pack(fill="x")
        self._header_icon = tk.PhotoImage(master=self.root, file=str(brand.UI_DIR / "header-icon.png"))
        ttk.Label(header, image=self._header_icon).pack(side="left")
        ttk.Label(header, text="Vital Sign", style="Wordmark.TLabel").pack(side="left", padx=(10, 0))
        self.status = ttk.Label(header, text="Checking…", style="Status.TLabel")
        self.status.pack(side="left", padx=(16, 0))
        self.toggle_btn = ttk.Button(header, text="Start", width=6, command=self.toggle)
        self.toggle_btn.pack(side="right")
        self.restart_btn = ttk.Button(header, text="Restart", command=self.restart)
        self.restart_btn.pack(side="right", padx=(0, 6))

        self.readout = Readout(outer, self.fonts, bg=window_bg)
        self.readout.pack(fill="x", pady=(16, 0))

        # Sources: heading, table, buttons, hint.
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(16, 6))
        ttk.Label(heading, text="Sources", style="Heading.TLabel").pack(side="left")
        ttk.Label(heading, text="Top is preferred; the rest are fallbacks", style="Muted.TLabel").pack(
            side="left", padx=(8, 0)
        )

        self.table = ttk.Treeview(outer, columns=("n", "device", "source"), show="headings", selectmode="browse", height=5)
        for col, title, width, stretch in (
            ("n", "#", 28, False),
            ("device", "Device", 240, True),
            ("source", "Source", SOURCE_COLUMN_PX, False),
        ):
            self.table.heading(col, text=title, anchor="w")
            self.table.column(col, width=width, minwidth=width if not stretch else 120, stretch=stretch, anchor="w")
        self.table.tag_configure("empty", foreground="gray")
        self.table.pack(fill="both", expand=True)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Scan for devices…", command=self.scan).pack(side="left")
        add = ttk.Menubutton(buttons, text="Add")
        menu = tk.Menu(add, tearoff=False)
        menu.add_command(label="Watch (HDS app)", command=lambda: self._add("hds"))
        menu.add_command(label="Demo readings", command=lambda: self._add("demo"))
        add["menu"] = menu
        add.pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Remove", command=self._remove).pack(side="right")
        ttk.Button(buttons, text="↓", width=3, command=lambda: self._move(1)).pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text="↑", width=3, command=lambda: self._move(-1)).pack(side="right", padx=(0, 6))

        self.hint = tk.Text(
            outer, width=1, height=1, wrap="word", font=self.fonts.small, foreground="gray", background=window_bg,
            borderwidth=0, highlightthickness=0, cursor="arrow", takefocus=False,
        )
        self.hint.tag_configure("mono", font=self.fonts.mono, foreground=TEXT_COLOR)
        self.hint.pack(fill="x", pady=(8, 0))
        self.hint.bind("<Configure>", lambda _e: self._fit_hint())

        # Recent log: one summary row, expandable to the last lines.
        ttk.Separator(outer).pack(fill="x", pady=(12, 0))
        log_row = ttk.Frame(outer, cursor="hand2")
        log_row.pack(fill="x", pady=(8, 0))
        self.log_arrow = ttk.Label(log_row, text="▶", style="Muted.TLabel")
        self.log_arrow.pack(side="left")
        ttk.Label(log_row, text="Recent log", style="Heading.TLabel").pack(side="left", padx=(6, 0))
        self.log_latest = ttk.Label(log_row, text="", style="Log.TLabel", width=1)
        self.log_latest.pack(side="left", fill="x", expand=True, padx=(8, 0))
        for widget in (log_row, *log_row.winfo_children()):
            widget.bind("<Button-1>", lambda _e: self.toggle_log())
        self.log = tk.Text(outer, height=8, wrap="none", font=self.fonts.mono_log)
        self.log.configure(state="disabled")
        self.log_open = False

    def toggle_log(self) -> None:
        self.log_open = not self.log_open
        self.log_arrow.configure(text="▼" if self.log_open else "▶")
        if self.log_open:
            self.log.pack(fill="both", expand=True, pady=(6, 0))
        else:
            self.log.pack_forget()

    # -- sources --------------------------------------------------------

    def _load_names(self) -> dict[str, str]:
        try:
            return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _device_name(self, spec: str) -> str:
        kind = parse_source(spec).kind
        if kind == "hds":
            return "Watch (HDS app)"
        if kind == "demo":
            return "Demo readings"
        if spec == "auto":
            return "Any one supported Bluetooth device"
        return self.names.get(spec, "")

    def _refresh_sources(self) -> None:
        selected = self._selected()
        self.table.delete(*self.table.get_children())
        for i, spec in enumerate(self.settings.sources):
            spec_shown = fit_text(spec, tkfont.nametofont("TkDefaultFont"), SOURCE_COLUMN_PX - 12)
            self.table.insert("", "end", iid=str(i), values=(i + 1, self._device_name(spec), spec_shown))
        if not self.settings.sources:
            self.table.insert("", "end", iid="empty", values=("", EMPTY_SOURCES, ""), tags=("empty",))
        if selected is not None and selected < len(self.settings.sources):
            self.table.selection_set(str(selected))
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        self.hint.configure(state="normal")
        self.hint.delete("1.0", "end")
        if any(parse_source(s).kind == "hds" for s in self.settings.sources):
            ip = lan_ip() or "<this computer's IP>"
            self.hint.insert("end", "HDS watch app: set the server to ")
            self.hint.insert("end", f"http://{ip}:{HDS_DEFAULT_PORT}", "mono")
            self.hint.insert("end", " (with the http://).\n")
        if self.bridge_up:
            self.hint.insert("end", "Changes apply the next time the bridge starts. Use Restart to apply them now.")
        self.hint.configure(state="disabled")
        self.root.after_idle(self._fit_hint)

    def _fit_hint(self) -> None:
        """Size the hint to its wrapped lines (Text has no auto-height).

        Runs from <Configure> and after_idle, so it must not force a layout
        pass itself, and only touches the height when it really changes;
        otherwise each resize would trigger the next.
        """
        # Counts the breaks between display lines, so one less than the lines.
        counted = self.hint.count("1.0", "end-1c", "displaylines")
        lines = 1 + ((counted[0] if isinstance(counted, tuple) else counted) or 0)
        if int(self.hint.cget("height")) != lines:
            self.hint.configure(height=lines)

    def hint_text(self) -> str:
        return self.hint.get("1.0", "end").strip()

    def _save(self) -> None:
        try:
            write_sources(CONFIG, self.settings.sources)
        except (OSError, ValueError) as e:
            messagebox.showerror("Vital Sign", f"Couldn't save {CONFIG.name}:\n{e}")
            self.settings = read_settings(CONFIG)  # show what's really saved
        self._refresh_sources()

    def _add(self, spec: str, name: str | None = None) -> None:
        if name:
            self.names[spec] = name
            try:
                NAMES_FILE.write_text(json.dumps(self.names, indent=1), encoding="utf-8")
            except OSError:
                pass
        if spec not in self.settings.sources:
            self.settings.sources.append(spec)
            self._save()

    def _selected(self) -> int | None:
        sel = self.table.selection() if hasattr(self, "table") else ()
        if not sel or not sel[0].isdigit():
            return None
        i = int(sel[0])
        return i if i < len(self.settings.sources) else None

    def _move(self, delta: int) -> None:
        i = self._selected()
        j = None if i is None else i + delta
        if j is None or not 0 <= j < len(self.settings.sources):
            return
        s = self.settings.sources
        s[i], s[j] = s[j], s[i]
        self._save()
        self.table.selection_set(str(j))

    def _remove(self) -> None:
        i = self._selected()
        if i is not None:
            del self.settings.sources[i]
            self._save()

    def scan(self) -> ScanDialog:
        return ScanDialog(self)

    # -- start / stop -----------------------------------------------------

    def _in_background(self, label: str, *scripts: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=label, foreground="")

        def work() -> None:
            errors = []
            for script in scripts:
                try:
                    result = run_script(script)
                    if result.returncode != 0:
                        errors.append((result.stderr or result.stdout).strip() or f"{script} exited {result.returncode}")
                except (OSError, subprocess.SubprocessError) as e:
                    errors.append(str(e))
            self.events.put(("done", errors))

        threading.Thread(target=work, daemon=True).start()

    def start(self) -> None:
        self._in_background("Starting…", "start_bridge")

    def stop(self) -> None:
        self._in_background("Stopping…", "stop_bridge")

    def restart(self) -> None:
        self._in_background("Restarting…", "stop_bridge", "start_bridge")

    def _stoppable(self) -> bool:
        return self.bridge_up or PIDFILE.exists()

    def toggle(self) -> None:
        self.stop() if self._stoppable() else self.start()

    # -- updates -----------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "bridge":
                    if value != self.bridge_up:
                        self.bridge_up = value
                        self._refresh_hint()
                    if not value:
                        self._show_reading(None)
                elif kind == "message":
                    self._on_message(value)
                elif kind == "done":
                    self.busy = False
                    if value:
                        messagebox.showerror("Vital Sign", "\n\n".join(value))
                elif kind == "scan":
                    value()
        except queue.Empty:
            pass
        self._update_status()
        self._age_readout()
        self._tail_log()
        self.root.after(300, self._poll)

    def _update_status(self) -> None:
        if self.busy:
            self.toggle_btn.state(["disabled"])
            self.restart_btn.state(["disabled"])
            return
        if self.bridge_up:
            self.status.configure(text="● Running", foreground=brand.RUNNING)
        elif PIDFILE.exists():
            self.status.configure(text="● Starting / not responding", foreground=brand.STARTING)
        else:
            self.status.configure(text="○ Stopped", foreground="")
        self.toggle_btn.configure(text="Stop" if self._stoppable() else "Start")
        self.toggle_btn.state(["!disabled"])
        self.restart_btn.state(["!disabled"] if self.bridge_up else ["disabled"])

    def _age_readout(self) -> None:
        """Dim the panel when readings stop, and keep the trend window moving."""
        now = time.monotonic()
        stale = not self.bridge_up or now - self.last_reading > STALE_S
        show_spo2 = now - self.last_spo2 <= SPO2_HIDE_S
        if stale != self.readout.stale or show_spo2 != self.readout.show_spo2 or now - self._last_trend_draw >= 5:
            self.readout.stale = stale
            self.readout.show_spo2 = show_spo2
            self._last_trend_draw = now
            self.readout.redraw()

    def _on_message(self, msg: dict) -> None:
        if msg.get("type") == "reading":
            self._show_reading(msg)
        elif msg.get("type") == "state":
            self.last_state = STATE_TEXT.get(msg.get("state"), msg.get("state", ""))
            self.readout.state_line = self.last_state
            self.readout.redraw()

    def _show_reading(self, msg: dict | None) -> None:
        r = self.readout
        if not msg:
            r.hr = r.spo2 = None
            r.state_line = "Bridge not running" if not self.bridge_up else self.last_state
            r.source_line = ""
        elif not msg.get("worn"):
            # Still a live message, so not stale -- the overlay treats it the
            # same way and shows "--" on an undimmed panel.
            self.last_reading = time.monotonic()
            r.stale = False
            r.hr = r.spo2 = None
            r.state_line = "Not worn"
            r.source_line = f"from {msg.get('source')}" if msg.get("source") else ""
        else:
            self.last_reading = time.monotonic()
            r.hr, r.spo2 = msg.get("hr"), msg.get("spo2")
            if r.hr:
                r.record(r.hr)
            if r.spo2:
                self.last_spo2 = time.monotonic()
                r.show_spo2 = True
            r.state_line = self.last_state or "Connected"
            sources = sorted({s for s in (msg.get("hr_source"), msg.get("spo2_source")) if s})
            r.source_line = "from " + " + ".join(sources) if sources else ""
            r.stale = False
            self._last_trend_draw = time.monotonic()
        r.redraw()

    def _tail_log(self) -> None:
        try:
            with LOGFILE.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == getattr(self, "_log_size", None):
                    return
                self._log_size = size
                f.seek(max(0, size - 4000))
                lines = f.read().decode("utf-8", "replace").splitlines()[-8:]
        except OSError:
            return
        if lines:
            latest, color = short_log_line(lines[-1])
            self.log_latest.configure(text=latest, foreground=color or "gray")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.configure(state="disabled")


class ScanDialog:
    """Scan for Bluetooth devices and add one as a source."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.devices: list[dict] = []
        self.win = tk.Toplevel(app.root)
        self.win.title("Scan for devices")
        self.win.transient(app.root)
        frame = ttk.Frame(self.win, padding=12)
        frame.pack(fill="both", expand=True)
        self.label = ttk.Label(frame, text="Scanning for 10 seconds…", wraplength=460, justify="left")
        self.label.pack(fill="x")
        self.listbox = tk.Listbox(frame, height=8, width=60, activestyle="none")
        self.listbox.pack(fill="both", expand=True, pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.add_btn = ttk.Button(row, text="Add selected", command=self._add, state="disabled")
        self.add_btn.pack(side="right")
        self.again_btn = ttk.Button(row, text="Scan again", command=self._start, state="disabled")
        self.again_btn.pack(side="right", padx=6)
        ttk.Button(row, text="Close", command=self.win.destroy).pack(side="left")
        self._start()

    def _start(self) -> None:
        self.add_btn.state(["disabled"])
        self.again_btn.state(["disabled"])
        self.listbox.delete(0, "end")
        note = ""
        if self.app.bridge_up:
            note = (
                "\nWhile the bridge is running, a device it's already connected to "
                "doesn't advertise, so it won't appear here."
            )
        self.label.configure(text="Scanning for 10 seconds…" + note)
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self) -> None:
        cmd = [sys.executable, str(ROOT / "bridge.py"), "--list-devices", "--json", "--scan-duration", "10"]
        try:
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW)
            if result.returncode != 0:
                devices, error = [], (result.stderr.strip().splitlines() or ["scan failed"])[-1]
            else:
                devices, error = json.loads(result.stdout or "[]"), None
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            devices, error = [], str(e)
        self.app.events.put(("scan", lambda: self._show(devices, error)))

    def _show(self, devices: list[dict], error: str | None) -> None:
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:  # dialog closed while the scan was running
            return
        self.devices = devices
        self.again_btn.state(["!disabled"])
        for d in devices:
            self.listbox.insert("end", f"{d['name'] or '(unnamed)'}   —   {d['description']}")
        if error:
            self.label.configure(text=f"Scan failed: {error}")
        elif not devices:
            self.label.configure(
                text="Nothing found. Wake the device (put it on, or press its button), make sure "
                "its phone app isn't connected to it, and scan again. For a watch, turn on its "
                "heart-rate broadcast first (on Garmin, Broadcast Heart Rate; for Apple Watch, a "
                "relay app such as ECHO, kept open on the iPhone)."
            )
        else:
            self.label.configure(text="Pick a device to add it as a source:")
            self.add_btn.state(["!disabled"])
            self.listbox.selection_set(0)

    def _add(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            d = self.devices[sel[0]]
            self.app._add(d["source"], d["name"])
        self.win.destroy()


class LiveFeed(threading.Thread):
    """Follows the bridge's WebSocket and hands messages to the UI thread."""

    def __init__(self, port: int, out: queue.Queue) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.out = out

    def run(self) -> None:
        asyncio.run(self._follow())

    async def _follow(self) -> None:
        while True:
            try:
                async with websockets.connect(f"ws://localhost:{self.port}", open_timeout=3) as ws:
                    self.out.put(("bridge", True))
                    async for message in ws:
                        self.out.put(("message", json.loads(message)))
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError, ValueError):
                pass
            self.out.put(("bridge", False))
            await asyncio.sleep(1.5)


_IGNORE_STATE = ["-ApplePersistenceIgnoreState", "YES"]


def skip_window_restoration() -> bool:
    """Stop macOS from offering to restore windows when Tk starts.

    After the window is force-quit or crashes, macOS asks on the next launch
    whether to reopen Python's windows -- with a modal alert a Tk app never
    shows, so tk.Tk() would hang forever waiting on it. Setting AppKit's
    ApplePersistenceIgnoreState in the argument domain skips restoration,
    which a Tk app doesn't use anyway. Call before tk.Tk(). Returns False if
    Foundation (pyobjc, which Bleak brings on macOS) isn't available.
    """
    if sys.platform != "darwin":
        return True
    try:
        from Foundation import NSArgumentDomain, NSUserDefaults
    except ImportError:
        return False
    defaults = NSUserDefaults.standardUserDefaults()
    domain = dict(defaults.volatileDomainForName_(NSArgumentDomain) or {})
    domain["ApplePersistenceIgnoreState"] = "YES"
    defaults.setVolatileDomain_forName_(domain, NSArgumentDomain)
    return True


def main() -> None:
    if not skip_window_restoration() and _IGNORE_STATE[0] not in sys.argv:
        # No Foundation: pass the same setting as a launch argument instead,
        # which AppKit only reads from the real process arguments.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], *_IGNORE_STATE])
    if WINDOWS:
        # Without an app ID of its own, the taskbar groups this window under
        # pythonw.exe and shows Python's icon instead of Vital Sign's.
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CleverTrou.VitalSign")
        except (OSError, AttributeError):
            pass
    # Before tk.Tk(): Tk only sees fonts that exist when it starts.
    brand_font = brand.register_brand_font()
    root = tk.Tk()
    App(root, brand_font=brand_font and brand.BRAND_FAMILY in tkfont.families(root))
    root.mainloop()


if __name__ == "__main__":
    main()
