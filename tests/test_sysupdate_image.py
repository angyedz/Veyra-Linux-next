from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-sysupdate-image.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_sysupdate_image", SourceFileLoader("veyra_sysupdate_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
sysupdate_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sysupdate_image)


class SysupdateImageTests(unittest.TestCase):
    def test_increments_the_last_numeric_version_component(self) -> None:
        self.assertEqual(sysupdate_image.next_version("0.1.9"), "0.1.10")

    def test_rejects_non_numeric_ci_fixture_versions(self) -> None:
        with self.assertRaises(RuntimeError):
            sysupdate_image.next_version("0.1.0-pre-alpha")

    def test_redirects_only_source_urls_for_local_signed_staging(self) -> None:
        transfer_dir = ROOT / "mkosi.extra/usr/lib/sysupdate.d"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            files = base / "serve"
            files.mkdir()
            definitions = sysupdate_image.write_test_definitions(
                files, transfer_dir, "http://127.0.0.1:8765/"
            )
            self.assertEqual(
                {path.name for path in definitions.iterdir()},
                {"10-root-verity.conf", "20-root.conf", "99-uki.conf"},
            )
            for path in definitions.glob("*.conf"):
                content = path.read_text(encoding="utf-8")
                self.assertIn("Path=http://127.0.0.1:8765/", content)
                self.assertIn("Verify=yes", content)
                self.assertNotIn(".raw.zst", content)
                if path.name == "99-uki.conf":
                    self.assertIn("Type=regular-file", content)
                else:
                    self.assertIn(".raw.xz", content)
                    self.assertIn("Type=partition", content)


if __name__ == "__main__":
    unittest.main()
