"""Smoke test for the control app's source editing (skipped without a display)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.destroy()
    HAVE_DISPLAY = True
except Exception:  # noqa: BLE001 -- no Tk, or no display (e.g. CI)
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY, "needs Tk and a display")
class GuiTest(unittest.TestCase):
    def setUp(self):
        import gui

        self.gui = gui
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            mock.patch.object(gui, "CONFIG", root / "config.sh"),
            mock.patch.object(gui, "NAMES_FILE", root / "names.json"),
            mock.patch.object(gui, "PIDFILE", root / ".bridge.pid"),
            mock.patch.object(gui, "LOGFILE", root / "bridge.log"),
            mock.patch.object(gui.LiveFeed, "start", lambda self: None),
        ]
        for p in self.patches:
            p.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root)

    def tearDown(self):
        self.root.destroy()
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_edits_are_saved_to_the_config_file(self):
        self.app._add("viatom:AA:BB:CC:DD:EE:FF", "O2M 3994")
        self.app._add("hds")
        self.app.listbox.selection_set(1)
        self.app._move(-1)
        self.assertIn('SOURCES="hds viatom:AA:BB:CC:DD:EE:FF"', self.gui.CONFIG.read_text())
        self.assertIn("O2M 3994", self.app.listbox.get(1))
        self.assertIn("http://", self.app.hint.cget("text"))
        self.app.listbox.selection_set(0)
        self.app._remove()
        self.assertIn('SOURCES="viatom:AA:BB:CC:DD:EE:FF"', self.gui.CONFIG.read_text())

    def test_status_when_stopped(self):
        self.app._update_status()
        self.assertEqual(self.app.status.cget("text"), "○ Stopped")
        self.assertIn("disabled", self.app.stop_btn.state())


if __name__ == "__main__":
    unittest.main()
