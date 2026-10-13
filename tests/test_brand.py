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

    def test_heart_fits_its_box(self):
        pts = brand.heart_points(10, 20, 30)
        xs, ys = pts[0::2], pts[1::2]
        self.assertGreaterEqual(min(xs), 10)
        self.assertLessEqual(max(xs), 40)
        self.assertGreaterEqual(min(ys), 20)
        self.assertLessEqual(max(ys), 50)
        self.assertEqual((pts[0], pts[1]), (pts[-2], pts[-1]))  # closed outline

    def test_gauge_leaves_a_gap_at_the_top(self):
        pts = brand.gauge_points(0, 0, 10)
        start, end = (pts[0], pts[1]), (pts[-2], pts[-1])
        self.assertLess(start[1], -9)  # near 12 o'clock
        self.assertLess(end[1], -9)
        self.assertGreater(start[0], 0)  # starts just right of the top...
        self.assertLess(end[0], 0)  # ...and ends just left of it

    def test_bundled_assets_exist(self):
        for f in brand.FONT_FILES:
            self.assertTrue((brand.FONT_DIR / f).exists(), f)
        self.assertTrue(brand.ICON_PNG.exists())
        self.assertTrue(brand.ICON_ICO.exists())


if __name__ == "__main__":
    unittest.main()
