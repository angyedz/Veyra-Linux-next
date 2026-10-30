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
        self.assertEqual(result.count("veyra_bios_ci=1"), 2)
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

    def test_bios_qemu_provides_virtio_gpu_without_default_vga(self) -> None:
        command = bios_boot.bios_qemu_command(
            Path("/tmp/test-disk.raw"), Path("/tmp/qemu-monitor.sock"), Path("/tmp/serial.log")
        )
        self.assertEqual(command[command.index("-vga") + 1], "none")
        self.assertEqual(command[command.index("-device") + 1], "virtio-vga")
        self.assertEqual(command[command.index("-display") + 1], "none")
        self.assertIn("snapshot=on", " ".join(command))
        self.assertNotIn("std", command)

    def test_vdri_failure_is_not_hidden_by_bios_boot_marker(self) -> None:
        valid = (
            bios_boot.BOOT_MARKER + b"\n"
            + bios_boot.GPU_MARKER
            + b"\n[  OK  ] Reached target \x1b[0;1;39mgraphical.target\x1b[0m - Graphical Interface.\n"
        )
        self.assertTrue(bios_boot.boot_evidence_verified(valid))
        self.assertFalse(bios_boot.boot_evidence_verified(bios_boot.BOOT_MARKER))
        self.assertFalse(bios_boot.boot_evidence_verified(bios_boot.GPU_MARKER))
        self.assertFalse(bios_boot.boot_evidence_verified(valid.replace(b"graphical.target", b"multi-user.target")))
        self.assertFalse(bios_boot.boot_evidence_verified(valid.replace(bios_boot.BOOT_MARKER, b"")))
        self.assertFalse(bios_boot.boot_evidence_verified(valid + b"\n" + bios_boot.GPU_FAILURE_MARKER))
        self.assertFalse(bios_boot.boot_evidence_verified(
            valid + b"\nvdri-qemu-smoke.service: Failed with result 'exit-code'."
        ))

    def test_bios_guest_marker_service_has_no_graphical_target_ordering_cycle(self) -> None:
        root = Path(__file__).resolve().parents[1]
        fixture = (root / "tests/fixtures/firmware/veyra-bios-ci.service").read_text(encoding="utf-8")
        self.assertIn("WantedBy=graphical.target", fixture)
        self.assertIn("After=multi-user.target", fixture)
        self.assertNotIn("\nAfter=graphical.target\n", fixture)
        self.assertIn("ConditionKernelCommandLine=veyra_bios_ci=1", fixture)
        self.assertIn("VEYRA_BIOS_BOOT_CI_PASS", fixture)

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
