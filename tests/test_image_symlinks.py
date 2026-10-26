"""Offline regression tests for image overlay symlink boundaries."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_image_symlinks", ROOT / "scripts/audit_image_symlinks.py")
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


class ImageSymlinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "overlay"
        (self.root / "usr/lib").mkdir(parents=True)

    def test_plain_files_pass(self):
        (self.root / "usr/lib/example").write_text("test")
        self.assertEqual(audit_module.audit(self.root), [])

    def test_internal_relative_symlink_passes(self):
        (self.root / "usr/lib/link").symlink_to("target")
        self.assertEqual(audit_module.audit(self.root), [])

    def test_relative_escape_rejected(self):
        (self.root / "usr/lib/link").symlink_to("../../../outside")
        self.assertEqual(len(audit_module.audit(self.root)), 1)

    def test_nested_escape_rejected(self):
        (self.root / "usr/lib/link").symlink_to(".././../../outside")
        self.assertEqual(len(audit_module.audit(self.root)), 1)

    def test_guest_absolute_symlink_allowed(self):
        (self.root / "usr/lib/link").symlink_to("/usr/lib/guest-target")
        self.assertEqual(audit_module.audit(self.root), [])

    def test_missing_overlay_rejected(self):
        self.assertTrue(audit_module.audit(self.root / "missing"))

    def test_symlink_overlay_root_rejected(self):
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertTrue(audit_module.audit(alias))


if __name__ == "__main__":
    unittest.main()
