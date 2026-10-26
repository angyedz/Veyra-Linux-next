"""Unit tests for the read-only boot profiler."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("profile_boot", ROOT / "scripts/profile-boot.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class BootProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_collect_all_three_reports(self):
        output = self.root / "reports"
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok\n", "")) as run:
            module.collect(output)
        self.assertEqual(run.call_count, 3)
        self.assertEqual({p.name for p in output.iterdir()}, set(module.COMMANDS))
        self.assertTrue(all(p.read_text() == "ok\n" for p in output.iterdir()))

    def test_fail_closed_on_command_failure(self):
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "error")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                module.collect(self.root / "reports")

    def test_reject_symlink_report_file(self):
        output = self.root / "reports"
        output.mkdir()
        (output / "boot-time.txt").symlink_to(self.root / "victim")
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")):
            with self.assertRaisesRegex(ValueError, "symlink"):
                module.collect(output)

    def test_failed_command_does_not_publish_partial_reports(self):
        output = self.root / "reports"
        responses = [
            subprocess.CompletedProcess([], 0, "ok", ""),
            subprocess.CompletedProcess([], 1, "", "unavailable"),
        ]
        with patch.object(module.subprocess, "run", side_effect=responses):
            with self.assertRaises(RuntimeError):
                module.collect(output)
        self.assertFalse(output.exists())

    def test_existing_report_is_never_overwritten(self):
        output = self.root / "reports"
        output.mkdir()
        (output / "boot-time.txt").write_text("previous run")
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "new", "")):
            with self.assertRaisesRegex(ValueError, "overwrite"):
                module.collect(output)
        self.assertEqual((output / "boot-time.txt").read_text(), "previous run")

    def test_reject_symlink_output_directory(self):
        output = self.root / "reports"
        real = self.root / "real"
        real.mkdir()
        output.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "directory"):
            module.collect(output)

    def test_reject_file_as_output_directory(self):
        output = self.root / "reports"
        output.write_text("file")
        with self.assertRaisesRegex(ValueError, "directory"):
            module.collect(output)


if __name__ == "__main__":
    unittest.main()
