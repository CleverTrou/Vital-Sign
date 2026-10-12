import tempfile
import unittest
from pathlib import Path

from vitalsign.config_file import read_settings, validate, write_sources


class ConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file(self):
        s = read_settings(self.root / "config.sh")
        self.assertEqual((s.sources, s.ws_port), ([], 8765))

    def test_sh_round_trip_keeps_other_settings(self):
        path = self.root / "config.sh"
        path.write_text('# my notes\nWS_PORT="8766"\n# SOURCES="old"\n')
        write_sources(path, ["viatom:AA:BB:CC:DD:EE:FF", "hds"])
        text = path.read_text()
        self.assertIn("# my notes", text)
        self.assertIn('# SOURCES="old"', text)
        s = read_settings(path)
        self.assertEqual((s.sources, s.ws_port), (["viatom:AA:BB:CC:DD:EE:FF", "hds"], 8766))
        write_sources(path, ["demo"])
        self.assertEqual(read_settings(path).sources, ["demo"])
        self.assertEqual(path.read_text().count("\nSOURCES="), 1)

    def test_ps1_round_trip(self):
        path = self.root / "config.ps1"
        path.write_text("$WsPort = 8770\n")
        write_sources(path, ["hrs:5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB", "hds:4000"])
        self.assertIn('$Sources = @("hrs:5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB", "hds:4000")', path.read_text())
        s = read_settings(path)
        self.assertEqual(s.sources, ["hrs:5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB", "hds:4000"])
        self.assertEqual(s.ws_port, 8770)

    def test_write_leaves_no_temp_file(self):
        path = self.root / "config.sh"
        write_sources(path, ["demo"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["config.sh"])

    def test_new_file_gets_a_header(self):
        path = self.root / "config.sh"
        write_sources(path, ["auto"])
        self.assertTrue(path.read_text().startswith("# Written by the Vital Sign app"))

    def test_legacy_address_becomes_first_source_and_is_retired(self):
        path = self.root / "config.sh"
        path.write_text('ADDRESS="ABC-123"\n')
        s = read_settings(path)
        self.assertEqual(s.sources, ["auto:ABC-123"])
        write_sources(path, s.sources + ["hds"])
        self.assertIn('# ADDRESS="ABC-123"', path.read_text())
        self.assertEqual(read_settings(path).sources, ["auto:ABC-123", "hds"])

    def test_refuses_anything_a_shell_could_interpret(self):
        for bad in ('hds"; rm -rf ~; "', "auto:$(id)", "auto:`id`", "demo extra", "pulsoid"):
            with self.assertRaises(ValueError, msg=bad):
                validate(bad)
        path = self.root / "config.sh"
        with self.assertRaises(ValueError):
            write_sources(path, ["demo", "auto:$(id)"])
        self.assertFalse(path.exists())

    def test_unsafe_values_in_an_existing_file_are_ignored(self):
        path = self.root / "config.sh"
        path.write_text('SOURCES="demo auto:$(id)"\n')
        self.assertEqual(read_settings(path).sources, ["demo"])


if __name__ == "__main__":
    unittest.main()
