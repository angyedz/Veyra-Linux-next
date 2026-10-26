"""Regression tests for the Fedora image source contract.

All mutations happen in temporary copies, never in the working image tree.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("veyra_smoke", ROOT / "scripts" / "smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SectionParserTests(unittest.TestCase):
    def test_ignores_comments_and_other_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp) / "mkosi.conf"
            config.write_text(
                "# header\n[Distribution]\nRelease=44 # inline comment\n"
                "[Content]\nPackages=kernel\n# Packages=bad\n"
                "[Output]\nFormat=disk\n", encoding="utf-8"
            )
            self.assertEqual(smoke.section_values(config, "Distribution"), ["Release=44"])
            self.assertEqual(smoke.section_values(config, "Content"), ["Packages=kernel"])
            self.assertEqual(smoke.section_values(config, "Missing"), [])

    def test_repeated_section_is_collected(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp) / "mkosi.conf"
            config.write_text("[Content]\nPackages=kernel\n[Output]\nFormat=disk\n"
                              "[Content]\nPackages=python3\n", encoding="utf-8")
            self.assertEqual(smoke.section_values(config, "Content"),
                             ["Packages=kernel", "Packages=python3"])


class SourceContractTests(unittest.TestCase):
    def test_repository_baseline_passes(self):
        self.assertEqual(smoke.main(), 0)

    def test_missing_required_package_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = (ROOT / "mkosi.conf").read_text(encoding="utf-8")
            (root / "mkosi.conf").write_text(
                config.replace("Packages=python3\n", "", 1), encoding="utf-8"
            )
            with patch.object(smoke, "ROOT", root):
                self.assertEqual(smoke.main(), 1)

    def test_wrong_distribution_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            config = (ROOT / "mkosi.conf").read_text(encoding="utf-8")
            (root / "mkosi.conf").write_text(
                config.replace("Distribution=fedora", "Distribution=debian", 1),
                encoding="utf-8"
            )
            with patch.object(smoke, "ROOT", root):
                self.assertEqual(smoke.main(), 1)


if __name__ == "__main__":
    unittest.main()
