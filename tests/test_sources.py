import unittest

from vitalsign.sources import SourceSpec, parse_source, resolve_specs


class ParseSourceTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(parse_source("auto"), SourceSpec("auto"))
        self.assertEqual(parse_source("HRS"), SourceSpec("hrs"))
        self.assertEqual(parse_source("demo"), SourceSpec("demo"))

    def test_mac_address_keeps_its_colons(self):
        self.assertEqual(parse_source("pc60:AA:BB:CC:DD:EE:FF").address, "AA:BB:CC:DD:EE:FF")

    def test_macos_uuid(self):
        spec = parse_source("auto:5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB")
        self.assertEqual(spec.address, "5B1F0E2A-6C3D-4E9B-9A1E-0123456789AB")

    def test_hds_port(self):
        self.assertEqual(parse_source("hds:4000").port, 4000)
        self.assertIsNone(parse_source("hds").port)

    def test_errors(self):
        for bad in ("pulsoid", "hds:abc", "hds:0", "demo:x", "viatom:"):
            with self.assertRaises(ValueError, msg=bad):
                parse_source(bad)


class ResolveSpecsTest(unittest.TestCase):
    def test_default_is_auto(self):
        self.assertEqual(resolve_specs(None, None), [SourceSpec("auto")])

    def test_legacy_address_flag_goes_first(self):
        specs = resolve_specs(["hds"], "ADDR")
        self.assertEqual(specs, [SourceSpec("auto", address="ADDR"), SourceSpec("hds")])

    def test_single_hds_only(self):
        with self.assertRaises(ValueError):
            resolve_specs(["hds", "hds:4000"], None)


if __name__ == "__main__":
    unittest.main()
