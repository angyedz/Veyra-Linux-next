"""Offline contract: restore Veyra Fastfetch and Glacier user-facing polish.

Package and install paths must match both mkosi and the legacy Lorax Live ISO.
Does not modify system packages or user homes during source tests.
"""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def mkosi_packages() -> set[str]:
    text = (ROOT / "mkosi.conf").read_text()
    return set(re.findall(r"(?m)^Packages=(\S+)$", text))


class VeyraLiveBrandingTests(unittest.TestCase):
    def test_fastfetch_is_preinstalled_for_live_and_installed_system(self):
        self.assertIn("fastfetch", mkosi_packages())
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        package_section = kickstart.split("%packages", 1)[1].split("%end", 1)[0]
        self.assertIn("fastfetch", package_section.splitlines())
        self.assertIn('test -x "$live_root/usr/bin/fastfetch"', kickstart)

    def test_fetch_config_uses_system_xdg_and_preserves_fedora_compatibility(self):
        paths = [
            ROOT / "branding/fastfetch/config.jsonc",
            ROOT / "mkosi.extra/usr/share/veyra/fastfetch/config.jsonc",
            ROOT / "mkosi.extra/etc/xdg/fastfetch/config.jsonc",
        ]
        self.assertEqual(len({p.read_bytes() for p in paths}), 1)
        config = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(config["logo"]["source"], "/usr/share/veyra/fastfetch/logo.txt")
        # Preserve the actual Fastfetch defaults; custom branding is logo-only.
        self.assertGreaterEqual(len(config["modules"]), 20)
        self.assertTrue(all(isinstance(module, str) for module in config["modules"]))
        self.assertIn("os", config["modules"])
        self.assertIn("memory", config["modules"])
        self.assertNotIn("display", config)
        self.assertEqual(config["logo"]["type"], "file")
        self.assertTrue((ROOT / "mkosi.extra/usr/share/veyra/fastfetch/logo.txt").is_file())
        self.assertNotIn("ID=veyra", paths[0].read_text(encoding="utf-8"))

    def test_live_installs_exact_global_preset_and_glacier_assets(self):
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        self.assertIn('"$live_root/etc/xdg/fastfetch/config.jsonc"', kickstart)
        self.assertIn('"$live_root/usr/share/veyra/fastfetch/logo.txt"', kickstart)
        self.assertIn('for theme_dir in color-schemes plasma/desktoptheme plasma/look-and-feel', kickstart)
        self.assertIn('for default in kdeglobals plasmarc kwinrc', kickstart)
        self.assertIn('"$live_root/usr/bin/veyra-theme"', kickstart)

    def test_dark_and_light_are_complete_and_toggleable(self):
        for flavor in ("dark", "light"):
            base = ROOT / f"mkosi.extra/usr/share/plasma/look-and-feel/org.veyra.glacier.{flavor}"
            self.assertEqual(
                json.loads((base / "metadata.json").read_text())["KPlugin"]["Id"],
                f"org.veyra.glacier.{flavor}",
            )
            self.assertTrue((base / "contents/defaults").is_file())
            self.assertGreater((base / "contents/previews/preview.png").stat().st_size, 1000)
            self.assertTrue((ROOT / f"mkosi.extra/usr/share/plasma/desktoptheme/veyra-glacier-{flavor}/metadata.json").is_file())
        self.assertIn("plasma-apply-lookandfeel", (ROOT / "mkosi.extra/usr/bin/veyra-theme").read_text())
        self.assertFalse((ROOT / "mkosi.extra/home").exists())


if __name__ == "__main__":
    unittest.main()
