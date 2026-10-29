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

    def test_task_frames_are_compact_square_without_decorative_corner_artifacts(self):
        import xml.etree.ElementTree as ET

        nine = ("topleft", "top", "topright", "left", "center", "right",
                "bottomleft", "bottom", "bottomright")
        states = ("normal", "hover", "focus", "attention", "minimized")
        for flavor in ("dark", "light"):
            path = ROOT / f"mkosi.extra/usr/share/plasma/desktoptheme/veyra-glacier-{flavor}/widgets/tasks.svg"
            root = ET.parse(path).getroot()
            # All nine slices describe the same 44 x 44 square, never a capsule.
            self.assertEqual(root.get("viewBox"), "0 0 116 54")
            groups = {element.get("id"): element for element in root if element.tag.endswith("g")}
            self.assertEqual(set(groups), {f"{state}-{part}" for state in states for part in nine})
            for state in states:
                center = groups[f"{state}-center"][0]
                self.assertEqual(
                    tuple(center.get(key) for key in ("x", "y", "width", "height")),
                    ("4", "4", "36", "36"),
                )
                for part in nine:
                    members = list(groups[f"{state}-{part}"])
                    self.assertGreaterEqual(len(members), 1)
                    self.assertLessEqual(len(members), 2)
                    for member in members:
                        self.assertIn(member.tag.rsplit("}", 1)[-1], ("rect", "path"))
                        if member.get("stroke"):
                            # Only a restrained focus rim; no neon/techno corners.
                            self.assertEqual(state, "focus")
                            self.assertEqual(member.get("stroke-width"), "1")
                            self.assertLessEqual(float(member.get("stroke-opacity")), 0.65)
            for edge in ("top", "right", "bottom", "left"):
                hint = next((el for el in root if el.get("id") == f"normal-hint-{edge}-margin"), None)
                self.assertIsNotNone(hint)
                self.assertEqual(hint.get("width"), "2")
                self.assertEqual(hint.get("height"), "2")
            self.assertNotIn("A12 12", path.read_text())
            self.assertNotIn("shadow-", path.read_text())

    def test_glacier_has_no_decorative_techno_outlines(self):
        import xml.etree.ElementTree as ET
        for flavor in ("dark", "light"):
            widgets = ROOT / f"mkosi.extra/usr/share/plasma/desktoptheme/veyra-glacier-{flavor}/widgets"
            for file in ("panel-background.svg", "background.svg", "button.svg"):
                root = ET.parse(widgets / file).getroot()
                self.assertFalse(any(el.get("stroke") for el in root.iter()),
                                 f"{flavor}/{file} unexpectedly contains outlines")

    def test_fastfetch_logo_is_vertically_offset(self):
        logo = ROOT / "mkosi.extra/usr/share/veyra/fastfetch/logo.txt"
        self.assertTrue(logo.read_text().startswith(chr(10) * 2))

    def test_user_home_is_untouched(self):
        self.assertFalse((ROOT / "mkosi.extra/home").exists())
        self.assertFalse((ROOT / "mkosi.extra/etc/skel/.config/kdeglobals").exists())


if __name__ == "__main__":
    unittest.main()
