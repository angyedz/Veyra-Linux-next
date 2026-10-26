"""Regression checks for Fastfetch assets copied into the mkosi image."""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("veyra_smoke", ROOT / "scripts/smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class BrandingMirrorTests(unittest.TestCase):
    def test_current_mirrors_pass(self):
        self.assertEqual(smoke.main(), 0)

    def test_stale_fastfetch_config_fails(self):
        self._assert_stale_mirror_fails("config.jsonc")

    def test_stale_fastfetch_logo_fails(self):
        self._assert_stale_mirror_fails("logo.txt")

    def _assert_stale_mirror_fails(self, filename: str):
        # Keep all unrelated source files intact; redirect only the image
        # mirror to an intentionally stale temporary file.
        relative = pathlib.Path("mkosi.extra/usr/share/veyra/fastfetch") / filename
        with tempfile.TemporaryDirectory() as tmp:
            stale = pathlib.Path(tmp) / filename
            stale.write_bytes(b"stale branding\n")
            original_read_bytes = pathlib.Path.read_bytes

            def read_bytes(path):
                if path == ROOT / relative:
                    return original_read_bytes(stale)
                return original_read_bytes(path)

            with patch.object(pathlib.Path, "read_bytes", read_bytes):
                self.assertEqual(smoke.main(), 1)


if __name__ == "__main__":
    unittest.main()
