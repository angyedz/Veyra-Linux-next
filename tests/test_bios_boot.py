from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/test-bios-boot.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_test_bios_boot", SourceFileLoader("veyra_test_bios_boot", str(SCRIPT))
)
assert SPEC and SPEC.loader
bios_boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bios_boot)


class BiosBootDiagnosticsTests(unittest.TestCase):
    def test_adds_serial_console_to_stable_and_trial_kernel_entries(self) -> None:
        config = (
            "menuentry stable {\n"
            "    if linux /VEYRA/stable.vmlinuz roothash=abc systemd.unit=graphical.target; then\n"
            "        if initrd /VEYRA/stable.initrd; then\n"
            "            return\n"
            "        fi\n"
            "    fi\n"
            "}\n"
            "menuentry trial {\n"
            "    if linux /VEYRA/trial.vmlinuz roothash=def systemd.unit=graphical.target; then\n"
            "        if initrd /VEYRA/trial.initrd; then\n"
            "            return\n"
            "        fi\n"
            "    fi\n"
            "}\n"
        )

        result = bios_boot.add_serial_diagnostics(config)

        self.assertEqual(result.count("console=ttyS0,115200n8"), 2)
        self.assertEqual(result.count("systemd.log_target=console"), 2)
        self.assertIn("roothash=abc console=tty0 console=ttyS0,115200n8", result)
        self.assertIn("roothash=def console=tty0 console=ttyS0,115200n8", result)

    def test_rejects_grub_config_without_both_deployment_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 2 Veyra GRUB kernel entries"):
            bios_boot.add_serial_diagnostics("menuentry { linux /kernel }\n")

    def test_rejects_duplicate_serial_diagnostics(self) -> None:
        config = (
            "if linux /stable.vmlinuz console=ttyS0,115200n8; then\n"
            "if linux /trial.vmlinuz; then\n"
        )

        with self.assertRaisesRegex(ValueError, "already contains serial diagnostics"):
            bios_boot.add_serial_diagnostics(config)

    def test_restores_the_grub_config_even_when_qemu_test_fails(self) -> None:
        original = (
            "if linux /stable.vmlinuz roothash=abc systemd.unit=graphical.target; then\n"
            "if linux /trial.vmlinuz roothash=def systemd.unit=graphical.target; then\n"
        ).encode("ascii")
        disk_state = {"config": original}

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "sfdisk":
                table = {
                    "partitiontable": {
                        "sectorsize": 512,
                        "partitions": [
                            {"type": bios_boot.EFI_SYSTEM_TYPE, "start": 2048}
                        ],
                    }
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(table), "")
            if command[0] == "mcopy" and "-o" not in command:
                Path(command[-1]).write_bytes(disk_state["config"])
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[0] == "mcopy" and "-o" in command:
                disk_state["config"] = Path(command[-2]).read_bytes()
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(f"unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "disk.raw"
            image.touch()
            with (
                patch.object(bios_boot.shutil, "which", return_value="/usr/bin/mcopy"),
                patch.object(bios_boot.subprocess, "run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic QEMU failure"):
                    with bios_boot.serial_console_diagnostics(image):
                        self.assertIn(b"console=ttyS0,115200n8", disk_state["config"])
                        raise RuntimeError("synthetic QEMU failure")

        self.assertEqual(disk_state["config"], original)


if __name__ == "__main__":
    unittest.main()
