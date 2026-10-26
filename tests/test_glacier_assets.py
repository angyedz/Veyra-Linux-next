"""Offline checks for both Plasma 6 desktop styles and global theme bundles."""
from __future__ import annotations

import configparser
import json
import struct
import zlib
from pathlib import Path
import xml.etree.ElementTree as ET
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "mkosi.extra/usr/share"
XDG = ROOT / "mkosi.extra/etc/xdg"
FRAMES = {"topleft", "top", "topright", "left", "center",
          "right", "bottomleft", "bottom", "bottomright"}
SVG_NS = "{http://www.w3.org/2000/svg}"


def load_ini(path: Path):
    config = configparser.ConfigParser(interpolation=None)
    with path.open(encoding="utf-8") as stream:
        config.read_file(stream)
    return config


def contrast(fg, bg):
    def luminance(rgb):
        channels = [int(x) / 255 for x in rgb.split(",")]
        def linear(v):
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = [linear(v) for v in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class GlacierAssetTests(unittest.TestCase):
    def test_color_contrast(self):
        for name in ("VeyraGlacier", "VeyraGlacierLight"):
            with self.subTest(name=name):
                cfg = load_ini(SHARE / f"color-schemes/{name}.colors")
                for section in ("Colors:Window", "Colors:View", "Colors:Button",
                                "Colors:Selection", "Colors:Tooltip", "Colors:Complementary"):
                    fg = cfg[section]["ForegroundNormal"]
                    bg = cfg[section]["BackgroundNormal"]
                    self.assertGreaterEqual(contrast(fg, bg), 4.5, (name, section))

    def test_global_theme_linkage(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                package_id = f"org.veyra.glacier.{mode}"
                name = "VeyraGlacier" if mode == "dark" else "VeyraGlacierLight"
                base = SHARE / "plasma/look-and-feel" / package_id
                meta = json.loads((base / "metadata.json").read_text())
                self.assertEqual(meta["KPackageStructure"], "Plasma/LookAndFeel")
                self.assertEqual(meta["KPlugin"]["Id"], package_id)
                self.assertEqual(base.name, meta["KPlugin"]["Id"])
                defaults = (base / "contents/defaults").read_text()
                for item in (f"ColorScheme={name}",
                             f"name=veyra-glacier-{mode}",
                             "ButtonsOnLeft=", "ButtonsOnRight=IAX",
                             "[plasmarc][Theme]", "[kdeglobals][General]"):
                    self.assertIn(item, defaults)

    def test_desktop_style_xml_frames(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                base = SHARE / f"plasma/desktoptheme/veyra-glacier-{mode}"
                meta = json.loads((base / "metadata.json").read_text())
                self.assertEqual(meta["KPlugin"]["Id"], base.name)
                self.assertEqual(load_ini(base / "plasmarc")["Settings"]["FallbackTheme"], "default")
                for relative in ("widgets/panel-background.svg", "widgets/background.svg",
                                 "widgets/tooltip.svg", "widgets/viewitem.svg",
                                 "widgets/tasks.svg", "dialogs/background.svg"):
                    root = ET.parse(base / relative).getroot()
                    self.assertEqual(root.tag, SVG_NS + "svg")
                    ids = [element.get("id") for element in root.iter() if element.get("id")]
                    self.assertEqual(len(ids), len(set(ids)), (mode, relative))
                    if relative.endswith(("panel-background.svg", "widgets/background.svg",
                                          "tooltip.svg", "dialogs/background.svg")):
                        self.assertTrue(FRAMES.issubset(set(ids)), (mode, relative))
                names = {node.get("id") for node in ET.parse(base / "widgets/tasks.svg").iter()}
                self.assertTrue({f"focus-{name}" for name in FRAMES}.issubset(names))
                names = {node.get("id") for node in ET.parse(base / "widgets/viewitem.svg").iter()}
                self.assertTrue({f"selected-{name}" for name in FRAMES}.issubset(names))

    def test_defaults_are_non_intrusive(self):
        kde = load_ini(XDG / "kdeglobals")
        plasma = load_ini(XDG / "plasmarc")
        kwin = load_ini(XDG / "kwinrc")
        self.assertEqual(kde["KDE"]["LookAndFeelPackage"], "org.veyra.glacier.dark")
        self.assertEqual(plasma["Theme"]["name"], "veyra-glacier-dark")
        self.assertEqual(kwin["org.kde.kdecoration2"]["ButtonsOnRight"], "IAX")
        self.assertEqual(kwin["org.kde.kdecoration2"]["ButtonsOnLeft"], "")
        self.assertFalse((ROOT / "mkosi.extra/home").exists())

    def test_day_night_wallpaper_package(self):
        base = SHARE / "wallpapers/VeyraGlacier"
        meta = json.loads((base / "metadata.json").read_text())
        self.assertEqual(meta["KPlugin"]["Id"], base.name)
        self.assertIn("Light", meta["X-KDE-PlasmaImageWallpaper-AccentColor"])
        self.assertIn("Dark", meta["X-KDE-PlasmaImageWallpaper-AccentColor"])
        for folder in ("images", "images_dark"):
            root = ET.parse(base / "contents" / folder / "1920x1080.svg").getroot()
            self.assertEqual(root.tag, SVG_NS + "svg")
            self.assertEqual(root.get("viewBox"), "0 0 1920 1080")
        for mode in ("light", "dark"):
            package = SHARE / f"plasma/look-and-feel/org.veyra.glacier.{mode}/contents/defaults"
            self.assertIn("Image=VeyraGlacier", package.read_text())
            style = SHARE / f"plasma/desktoptheme/veyra-glacier-{mode}/plasmarc"
            self.assertEqual(load_ini(style)["Wallpaper"]["defaultWallpaperTheme"], "VeyraGlacier")

    def test_global_theme_preview_png_integrity(self):
        for mode in ("dark", "light"):
            image = SHARE / f"plasma/look-and-feel/org.veyra.glacier.{mode}/contents/previews/preview.png"
            data = image.read_bytes()
            self.assertTrue(data.startswith(bytes.fromhex("89504e470d0a1a0a")), mode)
            offset, chunks, compressed = 8, [], bytearray()
            while offset < len(data):
                size = struct.unpack(">I", data[offset:offset + 4])[0]
                name = data[offset + 4:offset + 8]
                payload = data[offset + 8:offset + 8 + size]
                checksum = struct.unpack(">I", data[offset + 8 + size:offset + 12 + size])[0]
                self.assertEqual(zlib.crc32(name + payload), checksum, (mode, name))
                chunks.append(name)
                if name == b"IDAT":
                    compressed.extend(payload)
                offset += 12 + size
            self.assertEqual(chunks[0], b"IHDR")
            self.assertEqual(chunks[-1], b"IEND")
            pixels = zlib.decompress(compressed)
            self.assertEqual(len(pixels), 264 * (420 + 1))
            self.assertEqual(offset, len(data))

    def test_safe_selector(self):
        selector = (ROOT / "mkosi.extra/usr/bin/veyra-theme").read_text()
        self.assertIn("plasma-apply-lookandfeel --apply", selector)
        self.assertIn("Run veyra-theme as the logged-in desktop user", selector)
        self.assertFalse(any(line.lstrip().startswith("sudo ") for line in selector.splitlines()))


if __name__ == "__main__":
    unittest.main()
