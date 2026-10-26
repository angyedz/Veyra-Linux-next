"""Negative regression tests for the mkosi scalar config audit."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_mkosi_config", ROOT / "scripts/audit_mkosi_config.py")
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)
SOURCE = (ROOT / "mkosi.conf").read_text(encoding="utf-8")


class MkosiConfigAuditTests(unittest.TestCase):
    def test_baseline(self):
        self.assertEqual(audit_module.audit(SOURCE), [])

    def test_duplicate_bootloader_is_rejected(self):
        tampered = SOURCE.replace("Bootloader=systemd-boot",
                                  "Bootloader=systemd-boot\nBootloader=grub", 1)
        self.assertTrue(any("duplicate [Content] Bootloader" in error
                            for error in audit_module.audit(tampered)))

    def test_duplicate_release_is_rejected(self):
        tampered = SOURCE.replace("Release=44", "Release=44\nRelease=45", 1)
        self.assertTrue(any("duplicate [Distribution] Release" in error
                            for error in audit_module.audit(tampered)))

    def test_missing_critical_setting_is_rejected(self):
        tampered = SOURCE.replace("RepositoryKeyFetch=yes\n", "", 1)
        self.assertIn("missing [Distribution] RepositoryKeyFetch",
                      audit_module.audit(tampered))

    def test_repeated_packages_are_allowed(self):
        self.assertGreater(SOURCE.count("Packages="), 2)
        self.assertEqual(audit_module.audit(SOURCE), [])

    def test_malformed_entry_is_rejected(self):
        tampered = SOURCE + "\nmalformed\n"
        self.assertTrue(any("malformed assignment" in error
                            for error in audit_module.audit(tampered)))

    def test_repeated_section_is_rejected(self):
        tampered = SOURCE + "\n[Content]\nPackages=git\n"
        self.assertTrue(any("repeated [Content]" in error
                            for error in audit_module.audit(tampered)))


if __name__ == "__main__":
    unittest.main()
