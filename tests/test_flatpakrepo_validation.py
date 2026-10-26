"""Offline negative cases for the vendored Flatpak remote validator."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_flatpakrepo", ROOT / "scripts/validate_flatpakrepo.py")
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
SOURCE = validator.REMOTE.read_text(encoding="utf-8")


class FlatpakRepoValidationTests(unittest.TestCase):
    def test_real_remote_passes(self):
        self.assertEqual(validator.validate(SOURCE), [])

    def test_http_or_untrusted_url_rejected(self):
        bad = SOURCE.replace("Url=https://dl.flathub.org/repo/", "Url=http://example.invalid/repo/")
        self.assertTrue(any("URL" in e for e in validator.validate(bad)))

    def test_missing_key_rejected(self):
        bad = "\n".join(line for line in SOURCE.splitlines() if not line.startswith("GPGKey="))
        self.assertTrue(any("GPGKey" in e for e in validator.validate(bad)))

    def test_corrupt_key_rejected(self):
        bad = "\n".join("GPGKey=not-a-key!!!" if line.startswith("GPGKey=") else line
                        for line in SOURCE.splitlines())
        self.assertTrue(any("base64" in e for e in validator.validate(bad)))

    def test_duplicate_url_rejected(self):
        bad = SOURCE + "\nUrl=https://dl.flathub.org/repo/\n"
        self.assertTrue(any("syntax" in e for e in validator.validate(bad)))

    def test_extra_section_rejected(self):
        bad = SOURCE + "\n[Another Remote]\nUrl=https://example.invalid/\n"
        self.assertTrue(any("section" in e for e in validator.validate(bad)))


if __name__ == "__main__":
    unittest.main()
