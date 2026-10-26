from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCHEME = ROOT / "mkosi.extra/usr/share/color-schemes/VeyraDark.colors"
DEFAULTS = ROOT / "mkosi.extra/etc/xdg/kdeglobals"


def read_groups(path: Path) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ValueError(f"malformed group on line {line_number}")
            current = line[1:-1]
            if current in groups:
                raise ValueError(f"duplicate group {current!r}")
            groups[current] = {}
            continue
        if current is None or "=" not in line:
            raise ValueError(f"malformed key/value on line {line_number}")
        key, value = line.split("=", 1)
        if key in groups[current]:
            raise ValueError(f"duplicate key {key!r} in {current!r}")
        groups[current][key] = value
    return groups


def rgb(value: str) -> tuple[int, int, int]:
    components = tuple(int(component) for component in value.split(","))
    if len(components) != 3 or any(component < 0 or component > 255 for component in components):
        raise ValueError(f"invalid RGB value: {value!r}")
    return components


def luminance(color: tuple[int, int, int]) -> float:
    channels = [component / 255 for component in color]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class PlasmaThemeTests(unittest.TestCase):
    def test_scheme_has_required_kde_palette_groups(self) -> None:
        groups = read_groups(SCHEME)
        expected = {
            "Colors:Button",
            "Colors:Complementary",
            "Colors:Header",
            "Colors:Header][Inactive",
            "Colors:Selection",
            "Colors:Tooltip",
            "Colors:View",
            "Colors:Window",
            "General",
            "KDE",
            "WM",
        }
        self.assertTrue(expected.issubset(groups))
        self.assertEqual(groups["General"]["ColorScheme"], "VeyraDark")
        self.assertEqual(groups["General"]["Name"], "Veyra Dark")

    def test_normal_text_and_selection_text_meet_wcag_aa_contrast(self) -> None:
        groups = read_groups(SCHEME)
        for name in ("Colors:Button", "Colors:Complementary", "Colors:Tooltip", "Colors:View", "Colors:Window"):
            group = groups[name]
            foreground = rgb(group["ForegroundNormal"])
            background = rgb(group["BackgroundNormal"])
            self.assertGreaterEqual(contrast(foreground, background), 4.5, name)
            self.assertGreaterEqual(contrast(rgb(group["ForegroundInactive"]), background), 4.5, name)
        selection = groups["Colors:Selection"]
        self.assertGreaterEqual(
            contrast(rgb(selection["ForegroundNormal"]), rgb(selection["BackgroundNormal"])),
            4.5,
            "Colors:Selection",
        )

    def test_unlocked_system_default_selects_installed_scheme(self) -> None:
        defaults = read_groups(DEFAULTS)
        self.assertEqual(defaults["General"]["ColorScheme"], "VeyraDark")
        self.assertNotIn("ColorScheme[$i]", defaults["General"])

    def test_all_scheme_rgb_values_are_well_formed(self) -> None:
        for name, values in read_groups(SCHEME).items():
            for key, value in values.items():
                if key.startswith(("Background", "Decoration", "Foreground", "active", "inactive")) or key == "Color":
                    rgb(value)


if __name__ == "__main__":
    unittest.main()
