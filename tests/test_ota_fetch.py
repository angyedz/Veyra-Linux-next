"""Offline tests for resumable OTA cache without external network."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("ota_fetch", ROOT / "scripts/ota_fetch.py")
assert SPEC and SPEC.loader
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)
from ota_chunks import plan


class Response(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def geturl(self):
        return 'https://example.invalid/releases/asset'


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "root.img"
        self.target.write_bytes(b"Veyra" * 3000)
        self.bundle = self.root / "bundle"
        m = plan(self.target, self.bundle)
        self.assets = fetch.required_assets(m)
        self.name, self.size = next(iter(self.assets.items()))
        self.payload = (self.bundle / self.name).read_bytes()
        self.cache = self.root / "cache"

    def test_download_and_cache_hit(self):
        with patch.object(fetch.urllib.request, "urlopen", return_value=Response(self.payload)) as request:
            self.assertEqual(fetch.fetch_asset("https://example.invalid/releases", self.cache, self.name, self.size), "downloaded")
            self.assertEqual(fetch.fetch_asset("https://example.invalid/releases", self.cache, self.name, self.size), "cached")
            self.assertEqual(request.call_count, 1)

    def test_complete_partial_is_promoted_without_network(self):
        self.cache.mkdir()
        (self.cache / (self.name + ".part")).write_bytes(self.payload)
        with patch.object(fetch.urllib.request, "urlopen") as request:
            self.assertEqual(fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size), "cached")
            request.assert_not_called()
        self.assertEqual((self.cache / self.name).read_bytes(), self.payload)
        self.assertFalse((self.cache / (self.name + ".part")).exists())

    def test_corrupt_complete_partial_restarts(self):
        self.cache.mkdir()
        (self.cache / (self.name + ".part")).write_bytes(b"X" * self.size)
        with patch.object(fetch.urllib.request, "urlopen", return_value=Response(self.payload)) as request:
            self.assertEqual(fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size), "downloaded")
            self.assertIsNone(request.call_args.args[0].get_header("Range"))

    def test_resume_range(self):
        self.cache.mkdir()
        cut = len(self.payload) // 2
        (self.cache / (self.name + ".part")).write_bytes(self.payload[:cut])
        response = Response(self.payload[cut:], 206, {"Content-Range": f"bytes {cut}-{self.size - 1}/{self.size}"})
        with patch.object(fetch.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size), "downloaded")
            self.assertEqual(request.call_args.args[0].get_header("Range"), f"bytes={cut}-")

    def test_server_ignores_range_restart(self):
        self.cache.mkdir()
        (self.cache / (self.name + ".part")).write_bytes(b"junk")
        with patch.object(fetch.urllib.request, "urlopen", return_value=Response(self.payload)):
            self.assertEqual(fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size), "downloaded")

    def test_wrong_range_rejected(self):
        self.cache.mkdir()
        (self.cache / (self.name + ".part")).write_bytes(b"old")
        with patch.object(fetch.urllib.request, "urlopen", return_value=Response(self.payload, 206, {"Content-Range": "bytes 0-1/2"})):
            with self.assertRaisesRegex(ValueError, "Content-Range"):
                fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size)

    def test_corrupt_download_rejected(self):
        with patch.object(fetch.urllib.request, "urlopen", return_value=Response(b"X" * self.size)):
            with self.assertRaisesRegex(ValueError, "integrity"):
                fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size)
        self.assertFalse((self.cache / self.name).exists())

    def test_https_required(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            fetch.fetch_asset("http://example.invalid", self.cache, self.name, self.size)

    def test_symlink_cache_rejected(self):
        (self.root / "real").mkdir()
        self.cache.symlink_to(self.root / "real")
        with self.assertRaisesRegex(ValueError, "symlink"):
            fetch.fetch_asset("https://example.invalid", self.cache, self.name, self.size)


if __name__ == "__main__":
    unittest.main()
