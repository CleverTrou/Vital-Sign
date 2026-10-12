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
import queue
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import websockets

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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.settings = read_settings(CONFIG)
        self.names: dict[str, str] = self._load_names()
        self.bridge_up = False
        self.busy = False

        root.title("Vital Sign")
        root.minsize(560, 520)
        self._build()
        self._refresh_sources()
        LiveFeed(self.settings.ws_port, self.events).start()
        self._poll()

    # -- layout -------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x", **pad)
        self.status = ttk.Label(top, text="Checking…", font=("", 13, "bold"))
        self.status.pack(side="left")
        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop)
        self.stop_btn.pack(side="right")
        self.start_btn = ttk.Button(top, text="Start", command=self.start)
        self.start_btn.pack(side="right", padx=(0, 6))
        self.restart_btn = ttk.Button(top, text="Restart", command=self.restart)
        self.restart_btn.pack(side="right", padx=(0, 6))

        live = ttk.LabelFrame(outer, text="Now showing", padding=10)
        live.pack(fill="x", **pad)
        self.hr = ttk.Label(live, text="♥ -- bpm", font=("", 26, "bold"))
        self.hr.pack(side="left")
        self.spo2 = ttk.Label(live, text="SpO₂ --", font=("", 26, "bold"))
        self.spo2.pack(side="left", padx=(28, 0))
        self.detail = ttk.Label(live, text="", foreground="gray")
        self.detail.pack(side="right")

        src = ttk.LabelFrame(outer, text="Sources (top is preferred; the rest are fallbacks)", padding=10)
        src.pack(fill="both", expand=True, **pad)
        listrow = ttk.Frame(src)
        listrow.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(listrow, height=6, activestyle="none", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(listrow)
        buttons.pack(side="left", fill="y", padx=(10, 0))
        for text, cmd in (
            ("Scan for devices…", self.scan),
            ("Add Apple Watch (HDS)", lambda: self._add("hds")),
            ("Add demo readings", lambda: self._add("demo")),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
            ("Remove", self._remove),
        ):
            ttk.Button(buttons, text=text, command=cmd).pack(fill="x", pady=2)
        self.hint = ttk.Label(src, text="", foreground="gray", wraplength=520, justify="left")
        self.hint.pack(fill="x", pady=(8, 0))

        logf = ttk.LabelFrame(outer, text="Recent log", padding=6)
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=7, wrap="none", font=("Menlo" if not WINDOWS else "Consolas", 10))
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # -- sources --------------------------------------------------------

    def _load_names(self) -> dict[str, str]:
        try:
            return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _label(self, spec: str) -> str:
        kind = parse_source(spec).kind
        name = self.names.get(spec)
        if kind == "hds":
            name = "Apple Watch (HDS app)"
        elif kind == "demo":
            name = "Demo readings"
        elif kind == "auto" and spec == "auto":
            name = "Any one supported Bluetooth device"
        return f"{name}   —   {spec}" if name else spec

    def _refresh_sources(self) -> None:
        selected = self.listbox.curselection()
        self.listbox.delete(0, "end")
        for i, spec in enumerate(self.settings.sources, 1):
            self.listbox.insert("end", f"{i}. {self._label(spec)}")
        if not self.settings.sources:
            self.listbox.insert("end", "(none: the bridge scans for one supported Bluetooth device)")
        if selected and selected[0] < len(self.settings.sources):
            self.listbox.selection_set(selected[0])
        hints = []
        if any(parse_source(s).kind == "hds" for s in self.settings.sources):
            ip = lan_ip() or "<this computer's IP>"
            hints.append(f"HDS watch app: set the server to http://{ip}:{HDS_DEFAULT_PORT} (with the http://).")
        if self.bridge_up:
            hints.append("Changes apply the next time the bridge starts. Use Restart to apply them now.")
        self.hint.configure(text="\n".join(hints))

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
        sel = self.listbox.curselection()
        return sel[0] if sel and sel[0] < len(self.settings.sources) else None

    def _move(self, delta: int) -> None:
        i = self._selected()
        j = None if i is None else i + delta
        if j is None or not 0 <= j < len(self.settings.sources):
            return
        s = self.settings.sources
        s[i], s[j] = s[j], s[i]
        self._save()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(j)

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
        self.status.configure(text=label)

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

    # -- updates -----------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "bridge":
                    self.bridge_up = value
                    if not value:
                        self._show_reading(None)
                    self._refresh_sources()
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
        self._tail_log()
        self.root.after(300, self._poll)

    def _update_status(self) -> None:
        if self.busy:
            return
        if self.bridge_up:
            self.status.configure(text="● Running", foreground="#2fa04f")
        elif PIDFILE.exists():
            self.status.configure(text="● Starting / not responding", foreground="#c98a00")
        else:
            self.status.configure(text="○ Stopped", foreground="")
        self.start_btn.state(["disabled"] if self.bridge_up or self.busy else ["!disabled"])
        self.stop_btn.state(["!disabled"] if (self.bridge_up or PIDFILE.exists()) and not self.busy else ["disabled"])
        self.restart_btn.state(["!disabled"] if self.bridge_up and not self.busy else ["disabled"])

    def _on_message(self, msg: dict) -> None:
        if msg.get("type") == "reading":
            self._show_reading(msg)
        elif msg.get("type") == "state":
            self.detail.configure(text=STATE_TEXT.get(msg.get("state"), msg.get("state", "")))

    def _show_reading(self, msg: dict | None) -> None:
        if not msg:
            self.hr.configure(text="♥ -- bpm")
            self.spo2.configure(text="SpO₂ --")
            self.detail.configure(text="Bridge not running" if not self.bridge_up else "")
            return
        if not msg.get("worn"):
            self.hr.configure(text="♥ -- bpm")
            self.spo2.configure(text="SpO₂ --")
            self.detail.configure(text=f"Not worn ({msg.get('source')})")
            return
        hr, spo2 = msg.get("hr"), msg.get("spo2")
        self.hr.configure(text=f"♥ {hr if hr else '--'} bpm")
        self.spo2.configure(text=f"SpO₂ {str(spo2) + '%' if spo2 else '--'}")
        sources = {s for s in (msg.get("hr_source"), msg.get("spo2_source")) if s}
        self.detail.configure(text="from " + " + ".join(sorted(sources)) if sources else "")

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
                "its phone app isn't connected to it, and scan again. For Apple Watch through "
                "ECHO, start a workout in ECHO and keep it open on the iPhone."
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


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
