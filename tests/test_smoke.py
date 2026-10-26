from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/smoke.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_image_smoke", SourceFileLoader("veyra_image_smoke", str(SCRIPT))
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class ImagePackageConfigTests(unittest.TestCase):
    def test_reads_packages_from_included_mkosi_configs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mkosi.conf").write_text(
                "[Include]\nInclude=base.conf\n[Content]\nPackages=top-level\n",
                encoding="utf-8",
            )
            (root / "base.conf").write_text(
                "[Include]\nInclude=packages/*.conf\n[Content]\nPackages=base-only\n",
                encoding="utf-8",
            )
            package_dir = root / "packages"
            package_dir.mkdir()
            (package_dir / "desktop.conf").write_text(
                "[Content]\nPackages=desktop-only\n", encoding="utf-8"
            )

            self.assertEqual(
                smoke.image_packages(root / "mkosi.conf"),
                {"top-level", "base-only", "desktop-only"},
            )

    def test_rejects_missing_included_mkosi_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "mkosi.conf"
            config.write_text("[Include]\nInclude=missing.conf\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "no matching files"):
                smoke.image_packages(config)


if __name__ == "__main__":
    unittest.main()
