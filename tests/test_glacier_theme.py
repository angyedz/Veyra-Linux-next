"""Offline contract checks for the Veyra Glacier KDE default theme."""
from pathlib import Path
import configparser
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCHEME = ROOT / "mkosi.extra/usr/share/color-schemes/VeyraGlacier.colors"
DEFAULTS = ROOT / "mkosi.extra/etc/xdg/kdeglobals"


class GlacierThemeTests(unittest.TestCase):
    def test_scheme_sections_and_rgb(self):
        config = configparser.ConfigParser(interpolation=None)
        self.assertTrue(config.read(SCHEME))
        for section in ("Colors:Window", "Colors:View", "Colors:Button",
                        "Colors:Selection", "Colors:Tooltip", "Colors:Complementary"):
            self.assertIn(section, config)
            for key in ("BackgroundNormal", "ForegroundNormal"):
                rgb = [int(x) for x in config[section][key].split(",")]
                self.assertEqual(len(rgb), 3)
                self.assertTrue(all(0 <= x <= 255 for x in rgb))

    def test_defaults_select_installed_scheme(self):
        defaults = configparser.ConfigParser(interpolation=None)
        defaults.read(DEFAULTS)
        self.assertEqual(defaults["General"]["ColorScheme"], SCHEME.stem)
        self.assertEqual(defaults["Icons"]["Theme"], "breeze-dark")

    def test_light_palette_and_distinct_background(self):
        light = configparser.ConfigParser(interpolation=None)
        dark = configparser.ConfigParser(interpolation=None)
        light.read(ROOT / "mkosi.extra/usr/share/color-schemes/VeyraGlacierLight.colors")
        dark.read(SCHEME)
        self.assertEqual(light["General"]["ColorScheme"], "VeyraGlacierLight")
        for section in ("Colors:Window", "Colors:View", "Colors:Button",
                        "Colors:Selection", "Colors:Tooltip", "Colors:Complementary"):
            self.assertIn(section, light)
            for key in ("BackgroundNormal", "ForegroundNormal"):
                self.assertTrue(all(0 <= int(x) <= 255 for x in light[section][key].split(",")))
        self.assertNotEqual(light["Colors:Window"]["BackgroundNormal"],
                            dark["Colors:Window"]["BackgroundNormal"])

    def test_conventional_right_side_titlebar_buttons(self):
        kwin = configparser.ConfigParser(interpolation=None)
        kwin.read(ROOT / "mkosi.extra/etc/xdg/kwinrc")
        buttons = kwin["org.kde.kdecoration2"]
        self.assertEqual(buttons["ButtonsOnLeft"], "")
        self.assertEqual(buttons["ButtonsOnRight"], "IAX")

    def test_user_home_is_untouched(self):
        self.assertFalse((ROOT / "mkosi.extra/home").exists())
        self.assertFalse((ROOT / "mkosi.extra/etc/skel/.config/kdeglobals").exists())


if __name__ == "__main__":
    unittest.main()
