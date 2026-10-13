import unittest

from vitalsign import brand


class BrandTest(unittest.TestCase):
    def test_spo2_severity_matches_the_overlay(self):
        self.assertEqual(brand.spo2_severity(95), (brand.SPO2_GOOD, ""))
        self.assertEqual(brand.spo2_severity(90), (brand.SPO2_WARN, "▲"))
        self.assertEqual(brand.spo2_severity(89)[0], brand.SPO2_BAD)

    def test_blend(self):
        self.assertEqual(brand.blend("#ffffff", "#000000", 0.2), "#333333")
        # The handoff's pre-blended tokens are rounded by eye ("≈"); they're
        # kept as given, but should stay close to what they stand for.
        for token, (color, opacity) in {
            brand.INK_MUTED: (brand.INK, 0.70),
            brand.DIVIDER: ("#ffffff", 0.20),
            brand.TREND_AREA: (brand.PULSE, 0.12),
        }.items():
            exact = brand.blend(color, brand.PANEL, opacity)
            diff = max(abs(int(token[i : i + 2], 16) - int(exact[i : i + 2], 16)) for i in (1, 3, 5))
            self.assertLessEqual(diff, 8, (token, exact))

    def test_bundled_assets_exist(self):
        for f in brand.FONT_FILES:
            self.assertTrue((brand.FONT_DIR / f).exists(), f)
        self.assertTrue(brand.ICON_PNG.exists())
        self.assertTrue(brand.ICON_ICO.exists())
        for name in ("header-icon.png", "heart.png", "heart-stale.png"):
            self.assertTrue((brand.UI_DIR / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
