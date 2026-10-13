"""Smoke test for the control app's source editing (skipped without a display)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gui

try:
    import tkinter as tk

    # Without this, a pending macOS window-restore prompt can hang Tk() --
    # see skip_window_restoration's docstring. Skip rather than risk it.
    if not gui.skip_window_restoration():
        raise RuntimeError("can't suppress the window-restore prompt")
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

    def rows(self):
        return [self.app.table.item(iid)["values"] for iid in self.app.table.get_children()]

    def test_edits_are_saved_to_the_config_file(self):
        self.app._add("viatom:AA:BB:CC:DD:EE:FF", "O2M 3994")
        self.app._add("hds")
        self.app._add("hds")  # re-adding is a no-op
        self.app.table.selection_set("1")
        self.app._move(-1)
        self.assertIn('SOURCES="hds viatom:AA:BB:CC:DD:EE:FF"', self.gui.CONFIG.read_text())
        self.assertEqual([r[1] for r in self.rows()], ["Watch (HDS app)", "O2M 3994"])
        self.assertIn("http://", self.app.hint_text())
        self.app.table.selection_set("0")
        self.app._remove()
        self.assertIn('SOURCES="viatom:AA:BB:CC:DD:EE:FF"', self.gui.CONFIG.read_text())

    def test_empty_list_row(self):
        self.assertEqual(self.app.table.get_children(), ("empty",))
        self.assertEqual(self.app._selected(), None)

    def test_long_specs_are_cut_with_an_ellipsis(self):
        spec = "viatom:5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB"
        self.app._add(spec)
        shown = self.rows()[0][2]
        self.assertTrue(shown.endswith("…") and spec.startswith(shown[:-1]))

    def test_failed_save_shows_what_is_really_saved(self):
        self.app._add("hds")
        with mock.patch.object(self.gui, "write_sources", side_effect=OSError("disk full")), mock.patch.object(
            self.gui.messagebox, "showerror"
        ) as showerror:
            self.app._add("demo")
        showerror.assert_called_once()
        self.assertEqual(self.app.settings.sources, ["hds"])
        self.assertEqual(len(self.rows()), 1)

    def test_start_stop_is_one_button(self):
        self.app._update_status()
        self.assertEqual(self.app.status.cget("text"), "○ Stopped")
        self.assertEqual(str(self.app.toggle_btn.cget("text")), "Start")
        self.assertIn("disabled", self.app.restart_btn.state())
        self.app.bridge_up = True
        self.app._update_status()
        self.assertEqual(str(self.app.toggle_btn.cget("text")), "Stop")
        self.assertNotIn("disabled", self.app.restart_btn.state())
        self.app.busy = True
        self.app._update_status()
        self.assertIn("disabled", self.app.toggle_btn.state())

    def test_readout_follows_readings(self):
        self.app.bridge_up = True
        self.app._show_reading({"type": "reading", "hr": 66, "spo2": 91, "worn": True, "hr_source": "Ring", "spo2_source": "Ring"})
        r = self.app.readout
        self.assertEqual((r.hr, r.spo2, r.source_line, r.stale), (66, 91, "from Ring", False))
        self.assertEqual(len(r.history), 1)
        self.app._show_reading({"type": "reading", "worn": False, "source": "Ring"})
        self.assertEqual((r.hr, r.state_line, r.stale), (None, "Not worn", False))

    def test_panel_width_does_not_depend_on_the_digits(self):
        r = self.app.readout
        r.winfo_width = lambda: 552
        r.show_spo2 = True
        widths = set()
        for hr, spo2 in ((None, None), (61, 99), (111, 100), (188, 88), (72, 91)):
            r.hr, r.spo2 = hr, spo2
            r.redraw()
            widths.add(round(r.panel_width, 3))
        self.assertEqual(len(widths), 1, widths)

    def test_spo2_section_only_while_spo2_arrives(self):
        r = self.app.readout
        r.winfo_width = lambda: 552
        self.app.bridge_up = True
        # Shown at first, like the overlay right after it loads...
        self.app._show_reading({"type": "reading", "hr": 70, "spo2": None, "worn": True, "hr_source": "Watch"})
        self.assertTrue(r.show_spo2)
        # ...then a heart-rate-only source (a watch or strap) loses it after 30 s.
        self.app.last_spo2 -= self.gui.SPO2_HIDE_S + 1
        self.app._age_readout()
        self.assertFalse(r.show_spo2)
        hr_only = r.panel_width
        # An SpO2 value shows it straight away, and the panel widens.
        self.app._show_reading({"type": "reading", "hr": 70, "spo2": 97, "worn": True, "hr_source": "Ring"})
        self.assertTrue(r.show_spo2)
        self.assertGreater(r.panel_width, hr_only)
        # A brief gap (ring settling) keeps it...
        self.app.last_spo2 -= self.gui.SPO2_HIDE_S - 5
        self.app._age_readout()
        self.assertTrue(r.show_spo2)
        # ...30 s without SpO2 hides it again.
        self.app.last_spo2 -= 10
        self.app._age_readout()
        self.assertFalse(r.show_spo2)
        self.assertEqual(r.panel_width, hr_only)

    def test_long_source_names_stay_beside_the_panel(self):
        r = self.app.readout
        r.winfo_width = lambda: 552
        self.app.bridge_up = True
        name = "A Very Long Bluetooth Device Name That Goes On " * 3
        self.app._show_reading({"type": "reading", "hr": 100, "spo2": 100, "worn": True, "hr_source": name})
        source = next(i for i in r.find_all() if r.type(i) == "text" and r.itemcget(i, "text").startswith("from"))
        text = r.itemcget(source, "text")
        self.assertTrue(text.endswith("…"))
        left = r.coords(source)[0]
        self.assertGreater(left, r.panel_width)  # outside the panel...
        self.assertLessEqual(left + self.app.fonts.small.measure(text), 552)  # ...and inside the window

    def test_trend_spans_the_width_from_its_first_reading(self):
        r = self.app.readout
        # The window is withdrawn in tests, so give the canvas its width
        # directly: forcing a layout pass (update_idletasks) on a hidden
        # canvas crashes Tk 9 on macOS.
        r.winfo_width = lambda: 552
        now = self.gui.time.monotonic()
        r.history = [(now - 20, 70), (now - 10, 75), (now, 72)]
        r.redraw()
        line = [i for i in r.find_all() if r.type(i) == "line" and len(r.coords(i)) == 6]
        xs = r.coords(line[-1])[0::2]
        x0, _y0, width, _h = r.trend_box
        self.assertAlmostEqual(xs[0], x0 + 4, delta=1)  # first reading at the left edge...
        self.assertAlmostEqual(xs[-1], x0 + width - 4, delta=2)  # ...newest at the right

    def test_panel_is_sized_to_its_numbers_like_the_overlay(self):
        r = self.app.readout
        r.winfo_width = lambda: 552
        r.hr, r.spo2 = 72, 98
        r.redraw()
        x0, _y0, width, _h = r.trend_box
        self.assertLess(r.panel_width, 552 - 100)  # leaves room beside it for the status
        self.assertAlmostEqual(x0 + width + r.PAD_X, r.panel_width)  # trend as wide as the row

    def test_log_summary_line(self):
        line, color = self.gui.short_log_line("2026-10-11 21:20:58,376 INFO Overlay connected (1 total)")
        self.assertEqual((line, color), ("21:20:58 Overlay connected (1 total)", None))
        line, color = self.gui.short_log_line("2026-10-11 21:20:58,376 WARNING hrs X: no data")
        self.assertEqual(line, "21:20:58 WARNING hrs X: no data")
        self.assertIsNotNone(color)
        self.assertFalse(self.app.log_open)
        self.app.toggle_log()
        self.assertTrue(self.app.log_open)


if __name__ == "__main__":
    unittest.main()
