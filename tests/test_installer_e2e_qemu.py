from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-installer-e2e-qemu.py"
SPEC = importlib.util.spec_from_file_location("veyra_installer_e2e_qemu", SCRIPT)
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


class InstallerE2eQemuSafetyTests(unittest.TestCase):
    def test_only_disposable_qcow2_is_passed_as_installer_block_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk, iso, code, vars_file = [
                root / name for name in
                ("install.qcow2", "live.iso", "OVMF_CODE.fd", "OVMF_VARS.fd")
            ]
            for item in (disk, iso, code, vars_file):
                item.write_bytes(b"disposable fixture")
            args = e2e.qemu_command(
                disk=disk, serial=root / "serial.log", agent=root / "qga.sock",
                code=code, vars_file=vars_file, iso=iso,
            )
            self.assertIn(f"if=none,id=ci-target,format=qcow2,file={disk}", args)
            self.assertIn(f"virtio-blk-pci,drive=ci-target,serial={e2e.TARGET_SERIAL}", args)
            self.assertIn(f"file={iso},if=ide,media=cdrom,format=raw,readonly=on", args)
            self.assertIn("org.qemu.guest_agent.0", " ".join(args))
            self.assertNotIn("/dev/", " ".join(args))
            self.assertNotIn("format=raw,file=" + str(disk), " ".join(args))
            self.assertIn("order=d,menu=off", args)
            installed_args = e2e.qemu_command(
                disk=disk, serial=root / "first.log", agent=root / "first.sock",
                code=code, vars_file=vars_file, iso=None,
            )
            self.assertIn("order=c,menu=off", installed_args)
            self.assertNotIn(str(iso), " ".join(installed_args))

    def test_refuses_symlink_or_non_qcow2_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk = root / "install.qcow2"
            disk.write_bytes(b"test")
            link = root / "escape.qcow2"
            link.symlink_to(disk)
            code, variables = root / "code.fd", root / "vars.fd"
            code.write_bytes(b"firmware")
            variables.write_bytes(b"firmware")
            for invalid in (link, root / "not-a-disk", root / "other.img"):
                with self.subTest(invalid=invalid), self.assertRaises(e2e.InstallQemuError):
                    e2e.qemu_command(
                        disk=invalid, serial=root / "serial", agent=root / "agent",
                        code=code, vars_file=variables, iso=None,
                    )

    def test_guest_erase_requires_vm_exact_serial_exact_size_and_preflight(self) -> None:
        script = e2e.guest_install_shell()
        self.assertIn("systemd-detect-virt --vm", script)
        self.assertIn(e2e.TARGET_SERIAL, script)
        self.assertIn("blockdev --getsize64 /dev/vda", script)
        self.assertIn(str(e2e.DISK_BYTES), script)
        self.assertIn("--preflight --target /dev/vda", script)
        self.assertIn("--confirm-device /dev/vda", script)
        self.assertIn("VEYRA_REAL_INSTALLER_QEMU_PASS", script)
        self.assertIn("exec >/run/veyra-ci-install.log 2>&1", script)

    def test_passwords_never_appear_in_qemu_command_or_logging_strings(self) -> None:
        credentials = e2e.guest_credentials().decode("utf-8").splitlines()
        self.assertEqual(credentials[0], "veyra-ci")
        self.assertEqual(len(credentials), 5)
        self.assertEqual(credentials[1], credentials[2])
        self.assertEqual(credentials[3], credentials[4])
        self.assertGreaterEqual(len(credentials[1]), 8)
        self.assertGreaterEqual(len(credentials[3]), 8)

    def test_firstboot_requires_real_verity_graphical_and_persistent_home(self) -> None:
        script = e2e.firstboot_shell()
        for required in (
            "roothash=[0-9a-f]{64}", "findmnt -no FSTYPE /)",
            "findmnt -no FSTYPE /home)", "display-manager.service",
            "graphical.target", "VEYRA_INSTALLED_UEFI_QEMU_PASS",
        ):
            self.assertIn(required, script)


if __name__ == "__main__":
    unittest.main()
