"""Source-level safety and Bash syntax for ISO firmware diagnostics."""
from __future__ import annotations

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qemu-live-iso-smoke.sh"


class QemuLiveIsoSmokeTests(unittest.TestCase):
    def test_bash_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_reject_missing_arguments_without_qemu(self) -> None:
        result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)

    def test_never_attaches_host_disks_or_network(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("media=cdrom,readonly=on", source)
        self.assertIn("unit=0,readonly=on", source)
        self.assertIn('cp -- "$vars" "$copied_vars"', source)
        self.assertIn("-nic none", source)
        self.assertIn("q35,accel=tcg", source)
        for forbidden in ("wipefs ", "mkfs.", "grub2-install ", "dd if=", "losetup ", "mount -o"):
            self.assertNotIn(forbidden, source)

    def test_captures_both_boot_paths_and_never_claims_desktop(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("for fw in bios uefi", source)
        self.assertIn("GUI, installer and installed system are NOT verified", source)
        self.assertIn("BIOS ISO boot entry absent", source)
        self.assertIn("UEFI ISO boot entry absent", source)


if __name__ == "__main__":
    unittest.main()
