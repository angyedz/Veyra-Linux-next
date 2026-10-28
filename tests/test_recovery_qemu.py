from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "recovery_qemu_smoke", ROOT / "scripts/test-recovery-uki-qemu.py"
)
assert spec is not None and spec.loader is not None
recovery_qemu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery_qemu)


class IndependentRecoveryQemuTests(unittest.TestCase):
    def test_verification_needs_menu_and_no_guest_crash(self) -> None:
        self.assertTrue(recovery_qemu.menu_verified(
            "Veyra Recovery — read-only diagnostics\nSelect an action: ".encode("utf-8")
        ))
        self.assertFalse(recovery_qemu.menu_verified(b"Veyra Recovery only"))
        self.assertFalse(recovery_qemu.menu_verified(b"Select an action: "))
        self.assertFalse(recovery_qemu.menu_verified(
            b"Veyra Recovery\nSelect an action:\nTraceback (most recent call last)"
        ))
        self.assertFalse(recovery_qemu.menu_verified(
            b"Veyra Recovery\nSelect an action:\nKernel panic - not syncing"
        ))
        self.assertFalse(recovery_qemu.menu_verified(
            b"Veyra Recovery\nSelect an action:\nRecovery could not complete the requested operation"
        ))

    def test_only_disposable_firmware_and_disk_are_given_to_qemu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cmd = recovery_qemu.qemu_command(
                base / "disposable.img", base / "serial.log",
                base / "OVMF_CODE.fd", base / "OVMF_VARS.fd",
            )
            self.assertEqual(cmd[cmd.index("-nic") + 1], "none")
            self.assertEqual(cmd[cmd.index("-display") + 1], "none")
            self.assertIn("snapshot=on", " ".join(cmd))
            self.assertIn("readonly=on", " ".join(cmd))
            self.assertIn(f"file={base / 'disposable.img'}", " ".join(cmd))
            self.assertNotIn("/dev/sd", " ".join(cmd))

    def test_disk_setup_formats_only_private_esp_offset_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            disk = Path(directory) / "new-private.img"
            uki = Path(directory) / "dummy.efi"
            uki.write_bytes(b"fixture")
            commands = []
            layout = json.dumps({
                "partitiontable": {"partitions": [{
                    "start": recovery_qemu.ESP_START,
                    "size": recovery_qemu.ESP_SECTORS,
                    "type": recovery_qemu.ESP_GUID,
                }]}
            })

            def fake_run(command, *, input_text=None):
                commands.append((command, input_text))
                if command[:2] == ["sfdisk", "--json"]:
                    return layout
                return ""

            with patch.object(recovery_qemu, "run", side_effect=fake_run):
                recovery_qemu.make_esp(disk, uki)
            self.assertEqual(disk.stat().st_size, recovery_qemu.DISK_BYTES)
            self.assertEqual([cmd[0][0] for cmd in commands],
                             ["sfdisk", "sfdisk", "mkfs.vfat", "mmd", "mcopy"])
            format_command = commands[2][0]
            self.assertEqual(format_command[format_command.index("--offset") + 1],
                             str(recovery_qemu.ESP_START))
            self.assertEqual(format_command[-1], str(recovery_qemu.ESP_SECTORS // 2))
            self.assertEqual(commands[-1][0][-1], "::/EFI/BOOT/BOOTX64.EFI")
            self.assertTrue(all(str(disk) in " ".join(item[0]) for item in commands))
            self.assertTrue(all(not any(arg.startswith("/dev/") for arg in item[0])
                                for item in commands))

    def test_refuses_reused_output_without_launching_qemu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "existing"
            report.mkdir()
            script = (ROOT / "scripts/test-recovery-uki-qemu.py").read_text(encoding="utf-8")
            self.assertIn('if args.report_dir.exists() or args.report_dir.is_symlink()', script)
            self.assertIn("TemporaryDirectory(prefix=\"veyra-recovery-qemu-\"", script)
            self.assertIn('module.validate_recovery_uki_metadata(path)', script)
            self.assertIn("if not passed:", script)
            self.assertNotIn("losetup", script)
            self.assertNotIn("mount /dev", script)


if __name__ == "__main__":
    unittest.main()
