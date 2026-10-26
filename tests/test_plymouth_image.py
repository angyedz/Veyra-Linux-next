"""Test installed-image Plymouth theme validator with synthetic guest roots."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_plymouth", ROOT / "scripts/check-plymouth-image.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class PlymouthImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.theme = root / "usr/share/plymouth/themes/veyra"
        self.theme.mkdir(parents=True)
        self.default = root / "usr/share/plymouth/themes/default.plymouth"
        (self.theme / "veyra.script").write_text("/* test */")
        (self.theme / "veyra.plymouth").write_text(
            "[Plymouth Theme]\nName=Veyra\nModuleName=script\n"
            f"[script]\nScriptFile={self.theme / 'veyra.script'}\n")
        self.default.symlink_to(self.theme / "veyra.plymouth")

    def test_valid_theme(self):
        self.assertEqual(mod.check(self.theme, self.default), [])

    def test_missing_script(self):
        (self.theme / "veyra.script").unlink()
        self.assertTrue(mod.check(self.theme, self.default))

    def test_wrong_module(self):
        descriptor = self.theme / "veyra.plymouth"
        descriptor.write_text(descriptor.read_text().replace("ModuleName=script", "ModuleName=details"))
        self.assertTrue(mod.check(self.theme, self.default))

    def test_default_points_elsewhere(self):
        self.default.unlink()
        self.default.symlink_to(self.theme / "veyra.script")
        self.assertTrue(mod.check(self.theme, self.default))

    def test_missing_default(self):
        self.default.unlink()
        self.assertTrue(mod.check(self.theme, self.default))


if __name__ == "__main__":
    unittest.main()
