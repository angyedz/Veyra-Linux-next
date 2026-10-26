"""Malformed OTA manifests must fail before creating an output image."""
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


class ManifestValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "target.img"
        self.target.write_bytes(b"target-image" * 200)
        self.bundle = self.root / "bundle"
        self.original = ota.plan(self.target, self.bundle)
        self.output = self.root / "restored.img"

    def reject(self, edit):
        manifest = json.loads(json.dumps(self.original))
        edit(manifest)
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            ota.reconstruct(self.bundle / "manifest.json", self.output)
        self.assertFalse(self.output.exists())

    def test_extra_chunk(self):
        self.reject(lambda m: m["chunks"].append(dict(m["chunks"][0])))

    def test_wrong_chunk_size(self):
        self.reject(lambda m: m["chunks"][0].update(size=ota.CHUNK_SIZE + 1))

    def test_negative_target_size(self):
        self.reject(lambda m: m.update(target_size=-1))

    def test_oversized_compressed_asset(self):
        self.reject(lambda m: m["chunks"][0].update(compressed_size=ota.MAX_ASSET_SIZE + 1))

    def test_nonhex_digest(self):
        self.reject(lambda m: m["chunks"][0].update(sha256="bad"))

    def test_base_chunk_without_base(self):
        self.reject(lambda m: m["chunks"][0].update(source="base"))

    def test_packed_manifest_rejects_unsafe_pack_path(self):
        def make_unsafe(manifest):
            manifest["format"] = "veyra-ota-chunks-v2"
            record = manifest["chunks"][0]
            record.update(
                pack="../outside.tar",
                pack_sha256="a" * 64,
                pack_size=1024,
                member=record["asset"],
            )
        self.reject(make_unsafe)

    def test_packed_manifest_rejects_inconsistent_pack_digest(self):
        def make_inconsistent(manifest):
            manifest["format"] = "veyra-ota-chunks-v2"
            record = manifest["chunks"][0]
            record.update(
                pack="chunks-" + "a" * 64 + ".tar",
                pack_sha256="b" * 64,
                pack_size=1024,
                member=record["asset"],
            )
        self.reject(make_inconsistent)

    def test_wrong_index(self):
        self.reject(lambda m: m["chunks"][0].update(index=2))

    def test_valid_manifest_roundtrip(self):
        ota.reconstruct(self.bundle / "manifest.json", self.output)
        self.assertEqual(self.output.read_bytes(), self.target.read_bytes())


if __name__ == "__main__":
    unittest.main()
