"""Structural SVG contract for Veyra Glacier Plasma controls.

Built against the KDE Plasma background SVG element reference:
https://develop.kde.org/docs/plasma/theme/theme-elements/
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PIECES = {"topleft", "top", "topright", "left", "center",
          "right", "bottomleft", "bottom", "bottomright"}
ASSETS = {
    "button": ("normal", "hover", "pressed", "focus",
               "toolbutton-hover", "toolbutton-pressed", "toolbutton-focus"),
    "lineedit": ("base", "hover", "focus"),
    "listitem": ("normal", "hover", "pressed", "section"),
    "plasmoidheading": ("header", "footer"),
}

class PlasmaControlsTests(unittest.TestCase):
    def test_frames_complete_in_both_modes(self):
        for mode in ("dark", "light"):
            base = ROOT / f"mkosi.extra/usr/share/plasma/desktoptheme/veyra-glacier-{mode}/widgets"
            for filename, states in ASSETS.items():
                with self.subTest(mode=mode, asset=filename):
                    tree = ET.parse(base / f"{filename}.svg")
                    ids = {node.get("id") for node in tree.iter() if node.get("id")}
                    for state in states:
                        self.assertTrue({f"{state}-{part}" for part in PIECES}.issubset(ids), state)
                    self.assertEqual(len(ids), sum(1 for node in tree.iter() if node.get("id")), "duplicate SVG IDs")
                    self.assertFalse(any(n.get("filter") for n in tree.iter()), "no GPU-expensive filters")

    def test_arrows(self):
        required = {"up-arrow", "down-arrow", "left-arrow", "right-arrow"}
        for mode in ("dark", "light"):
            arrow = ROOT / f"mkosi.extra/usr/share/plasma/desktoptheme/veyra-glacier-{mode}/widgets/arrows.svg"
            root = ET.parse(arrow).getroot()
            ids = {n.get("id") for n in root.iter()}
            self.assertTrue(required.issubset(ids))

    def test_ensure_titlebar_layout_untouched(self):
        kwinrc = (ROOT / "mkosi.extra/etc/xdg/kwinrc").read_text()
        self.assertIn("ButtonsOnRight=IAX", kwinrc)
        self.assertIn("ButtonsOnLeft=", kwinrc)
        self.assertNotIn("ButtonsOnRight=IAXM", kwinrc)

if __name__ == "__main__":
    unittest.main()
