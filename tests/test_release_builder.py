"""Exercise the single-entrypoint release builder without publishing assets."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("veyra_release", ROOT / "scripts/veyra-release.py")
assert SPEC and SPEC.loader
release_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_module)


class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "old.img"
        self.target = self.root / "new.img"
        self.output = self.root / "release"
        self.base.write_bytes(b"A" * 4096)
        self.target.write_bytes(b"A" * 4095 + b"B")

    def test_full_and_delta_bundle_verified(self):
        m = release_module.release(self.target, self.output, "0.1.0",
                                   self.base, "0.0.9")
        self.assertEqual(m["verified_variants"], ["full", "delta"])
        self.assertEqual(m["status"], "UNSIGNED-EXPERIMENTAL-NOT-INSTALLABLE")
        self.assertEqual(json.loads((self.output / "release.json").read_text())["version"], "0.1.0")
        self.assertTrue((self.output / "ota-full.json").exists())
        self.assertTrue((self.output / "ota-delta.json").exists())
        self.assertTrue(all((self.output / name).is_file() for name in m["files"]))
        self.assertFalse(any(p.is_dir() for p in self.output.iterdir()))

    def test_first_release_full_only(self):
        m = release_module.release(self.target, self.output, "0.1.0")
        self.assertEqual(m["preferred_transport"], "full")
        self.assertEqual(m["verified_variants"], ["full"])
        self.assertFalse((self.output / "ota-delta.json").exists())

    def test_output_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            release_module.release(self.target, self.output, "0.1.0")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_version_rejected(self):
        with self.assertRaisesRegex(ValueError, "version"):
            release_module.release(self.target, self.output, "../escape")
        self.assertFalse(self.output.exists())

    def test_base_requires_version(self):
        with self.assertRaisesRegex(ValueError, "base-version"):
            release_module.release(self.target, self.output, "0.1.0", self.base)
        self.assertFalse(self.output.exists())

    def test_tiny_asset_limit_fails_without_publishing(self):
        with self.assertRaises(ValueError):
            release_module.release(self.target, self.output, "0.1.0", max_asset_size=8)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
