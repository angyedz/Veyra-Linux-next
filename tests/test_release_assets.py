"""Unit tests for release-asset policy; fixtures never touch real releases."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "scripts/verify-release-assets.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseAssetTests(unittest.TestCase):
    def make_fixture(self, directory: Path):
        v, digest = "0.1.1", "a" * 64
        names = [
            f"veyra-next_{v}.efi",
            f"veyra-next_{v}.vmlinuz",
            f"veyra-next_{v}.initrd",
            f"veyra-next_{v}_{digest}.root.raw.zst",
            f"veyra-next_{v}_{digest}.root-verity.raw.zst",
        ]
        sums = []
        for name in names:
            content = ("fixture:" + name).encode()
            (directory / name).write_bytes(content)
            sums.append(hashlib.sha256(content).hexdigest() + "  " + name)
        (directory / "SHA256SUMS").write_text("\\n".join(sums) + "\\n", encoding="ascii")
        (directory / "SHA256SUMS.gpg").write_bytes(b"fake signature: actual GPG verification is separate")
        return v, names

    def test_valid_pair_and_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            v, _ = self.make_fixture(p)
            self.assertEqual(len(release.validate(p, v)), 7)

    def test_wrong_version_and_mutated_file_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            v, names = self.make_fixture(p)
            with self.assertRaisesRegex(ValueError, "expected exactly one"):
                release.validate(p, "0.1.2")
            (p / names[0]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                release.validate(p, v)

    def test_oversize_asset_rejected_without_allocating_gigabytes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            v, _ = self.make_fixture(p)
            with patch.object(release, "MAX_ASSET_BYTES", 32):
                with self.assertRaisesRegex(ValueError, "2 GB"):
                    release.validate(p, v)

    def test_mixed_root_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            v, names = self.make_fixture(p)
            (p / names[4]).rename(p / names[4].replace("a" * 64, "b" * 64))
            with self.assertRaisesRegex(ValueError, "one identical"):
                release.validate(p, v)


if __name__ == "__main__":
    unittest.main()
