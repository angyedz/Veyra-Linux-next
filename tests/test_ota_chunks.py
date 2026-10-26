"""Offline OTA chunk format roundtrip and corruption tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ota_chunks", ROOT / "scripts/ota_chunks.py")
assert SPEC and SPEC.loader
ota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ota)


class OTAChunkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "base.img"
        self.target = self.root / "target.img"
        self.bundle = self.root / "bundle"
        self.restored = self.root / "restored.img"

    def test_changed_chunks_only_and_roundtrip(self):
        a = b"A" * ota.CHUNK_SIZE
        b = b"B" * ota.CHUNK_SIZE
        c = b"C" * 1024
        self.base.write_bytes(a + b + c)
        self.target.write_bytes(a + b"Z" * ota.CHUNK_SIZE + c)
        manifest = ota.plan(self.target, self.bundle, self.base)
        self.assertEqual([r["source"] for r in manifest["chunks"]], ["base", "asset", "base"])
        self.assertEqual(len(list(self.bundle.glob("*.zlib"))), 1)
        ota.reconstruct(self.bundle / "manifest.json", self.restored, self.base)
        self.assertEqual(self.restored.read_bytes(), self.target.read_bytes())

    def test_full_image_without_base(self):
        self.target.write_bytes(b"new-system" * 300)
        ota.plan(self.target, self.bundle)
        ota.reconstruct(self.bundle / "manifest.json", self.restored)
        self.assertEqual(self.restored.read_bytes(), self.target.read_bytes())

    def test_wrong_base_rejected(self):
        self.base.write_bytes(b"old")
        self.target.write_bytes(b"new")
        ota.plan(self.target, self.bundle, self.base)
        self.base.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "base image hash mismatch"):
            ota.reconstruct(self.bundle / "manifest.json", self.restored, self.base)
        self.assertFalse(self.restored.exists())

    def test_corrupt_asset_rejected_and_partial_removed(self):
        self.target.write_bytes(b"hello" * 100)
        m = ota.plan(self.target, self.bundle)
        asset = self.bundle / m["chunks"][0]["asset"]
        asset.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            ota.reconstruct(self.bundle / "manifest.json", self.restored)
        self.assertFalse(self.restored.exists())

    def test_limit_rejected(self):
        self.target.write_bytes(bytes(range(256)) * 10)
        with self.assertRaisesRegex(ValueError, "asset limit"):
            ota.plan(self.target, self.bundle, max_asset_size=ota.MAX_ASSET_SIZE + 1)
        with self.assertRaisesRegex(ValueError, "exceeds asset limit"):
            ota.plan(self.target, self.bundle, max_asset_size=10)

    def test_existing_output_not_overwritten(self):
        self.target.write_bytes(b"new")
        ota.plan(self.target, self.bundle)
        self.restored.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            ota.reconstruct(self.bundle / "manifest.json", self.restored)
        self.assertEqual(self.restored.read_bytes(), b"keep")

    def test_path_traversal_in_asset_name_rejected(self):
        self.target.write_bytes(b"new")
        m = ota.plan(self.target, self.bundle)
        m["chunks"][0]["asset"] = "../outside.zlib"
        (self.bundle / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "unsafe"):
            ota.reconstruct(self.bundle / "manifest.json", self.restored)


if __name__ == "__main__":
    unittest.main()
