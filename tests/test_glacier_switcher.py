"""Executable integration tests of the real unprivileged Veyra theme selector.

Uses fake KDE binaries in an isolated PATH; no settings outside the temporary
directory are written and a real Plasma session is never manipulated.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "mkosi.extra/usr/bin/veyra-theme"


class GlacierSwitcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.record = self.root / "selection.txt"
        kde = self.bin / "plasma-apply-lookandfeel"
        kde.write_text(
            '#!/bin/sh\n'
            'case "$1" in\n'
            '  --list) printf "%s\\n" "org.veyra.glacier.dark" "org.veyra.glacier.light" ;;\n'
            '  --apply) printf "%s\\n" "$2" > "$VEYRA_TEST_LOG" ;;\n'
            '  *) exit 4 ;;\n'
            'esac\n'
        )
        kde.chmod(0o755)
        reader = self.bin / "kreadconfig6"
        reader.write_text('#!/bin/sh\nprintf "%s\\n" "org.veyra.glacier.dark"\n')
        reader.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment.update(
            PATH=str(self.bin) + os.pathsep + self.environment.get("PATH", ""),
            WAYLAND_DISPLAY="wayland-test-mock",
            VEYRA_TEST_LOG=str(self.record),
            HOME=str(self.root),
            XDG_CONFIG_HOME=str(self.root / "config"),
        )
        self.environment.pop("DISPLAY", None)

    def run_theme(self, *args, env=None):
        return subprocess.run(
            ["bash", str(SELECTOR), *args],
            env=env or self.environment,
            text=True, capture_output=True, check=False,
            timeout=10,
        )

    def test_can_read_status_without_changing_config(self):
        result = self.run_theme("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "org.veyra.glacier.dark")
        self.assertFalse(self.record.exists())

    @unittest.skipIf(os.geteuid() == 0, "root must be refused by the selector")
    def test_switches_correct_global_theme(self):
        for name in ("light", "dark"):
            with self.subTest(name=name):
                result = self.run_theme(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.record.read_text().strip(), f"org.veyra.glacier.{name}"
                )
        self.assertFalse((self.root / "config" / "kdeglobals").exists())

    def test_unknown_mode_never_invokes_kde(self):
        result = self.run_theme("invalid")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.record.exists())

    def test_headless_mode_never_invokes_kde(self):
        env = self.environment.copy()
        env.pop("WAYLAND_DISPLAY")
        result = self.run_theme("light", env=env)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.record.exists())

    def test_help_never_invokes_kde(self):
        result = self.run_theme("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("light", result.stdout)
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
