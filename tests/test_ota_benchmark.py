"""Compare actual compressed OTA transfer sizes, not raw changed bytes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("benchmark_ota", ROOT / "scripts/benchmark_ota.py")
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class OTABenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "base.img"
        self.target = self.root / "target.img"
        self.bundle = self.root / "bundle"

    def test_small_change_prefers_delta(self):
        import random
        random_bytes = random.Random(42).randbytes(4 * 1024 * 1024)
        self.base.write_bytes(random_bytes + random_bytes)
        self.target.write_bytes(random_bytes + b"X" * (4 * 1024 * 1024))
        result = benchmark.measure(self.base, self.target, self.bundle)
        self.assertEqual(result["recommendation"], "delta")
        self.assertEqual(result["reused_chunks"], 1)
        self.assertTrue((self.bundle / "benchmark.json").exists())

    def test_entirely_changed_data_prefers_full(self):
        import random
        self.base.write_bytes(random.Random(1).randbytes(256 * 1024))
        self.target.write_bytes(random.Random(2).randbytes(256 * 1024))
        result = benchmark.measure(self.base, self.target, self.bundle)
        self.assertEqual(result["recommendation"], "full")
        self.assertEqual(result["reused_chunks"], 0)

    def test_invalid_threshold(self):
        self.base.write_bytes(b"a")
        self.target.write_bytes(b"b")
        with self.assertRaises(ValueError):
            benchmark.measure(self.base, self.target, self.bundle, threshold=0)


if __name__ == "__main__":
    unittest.main()
