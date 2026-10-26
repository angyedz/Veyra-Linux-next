"""Exercise the single-entrypoint release builder without publishing assets."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        full_manifest = json.loads((self.output / "ota-full.json").read_text())
        delta_manifest = json.loads((self.output / "ota-delta.json").read_text())
        self.assertEqual(full_manifest["format"], "veyra-ota-chunks-v2")
        self.assertEqual(delta_manifest["format"], "veyra-ota-chunks-v2")
        self.assertTrue((self.output / "ota-full.json").exists())
        self.assertTrue((self.output / "ota-delta.json").exists())
        self.assertTrue(all((self.output / name).is_file() for name in m["files"]))
        self.assertFalse(any(p.is_dir() for p in self.output.iterdir()))
        self.assertFalse(any(p.suffix == ".zlib" for p in self.output.iterdir()))
        self.assertEqual(m["asset_count"], len(m["files"]) + 1)
        self.assertLessEqual(m["asset_count"], 1000)

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

    def test_release_packs_many_chunks_into_bounded_assets(self):
        target = self.root / "many-chunks.img"
        target.write_bytes(b"A" * 4 * 1024 * 1024 + b"B" * 4 * 1024 * 1024)
        with patch.object(release_module, "PACK_TARGET_SIZE", 4096):
            result = release_module.release(target, self.output, "0.1.0")

        packs = sorted(self.output.glob("chunks-*.tar"))
        self.assertEqual(len(packs), 2)
        self.assertTrue(all(pack.stat().st_size <= release_module.MAX_PACK_SIZE for pack in packs))
        self.assertTrue(all(record.get("pack") for record in json.loads(
            (self.output / "ota-full.json").read_text()
        )["chunks"] if record["source"] == "asset"))
        self.assertEqual(result["asset_count"], 4)
        self.assertFalse(any(path.suffix == ".zlib" for path in self.output.iterdir()))

    def test_release_refuses_to_exceed_github_asset_count(self):
        target = self.root / "many-chunks.img"
        target.write_bytes(b"A" * 4 * 1024 * 1024 + b"B" * 4 * 1024 * 1024)
        with patch.object(release_module, "PACK_TARGET_SIZE", 4096), \
             patch.object(release_module, "MAX_GITHUB_RELEASE_ASSETS", 3):
            with self.assertRaisesRegex(ValueError, "asset count"):
                release_module.release(target, self.output, "0.1.0")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
