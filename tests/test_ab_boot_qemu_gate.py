from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/test-ab-boot.py"
SPEC = importlib.util.spec_from_loader("veyra_ab_qemu", SourceFileLoader("veyra_ab_qemu", str(SCRIPT)))
assert SPEC and SPEC.loader
ota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ota)


class QemuGuestVpkgGateTests(unittest.TestCase):
    def test_detects_shell_trap_and_systemd_failure_without_waiting_for_timeout(self) -> None:
        samples = (
            "VEYRA_VPKG_CI_FAILURE status=1 line=24",
            "veyra-vpkg-ci.service: Failed with result 'exit-code'.",
            "veyra-vpkg-ci.service: Main process exited, code=exited, status=1/FAILURE",
            "\x1b[0;1;38:5:185mveyra-vpkg-ci.service: Failed with result 'exit-code'.\x1b[0m\r",
        )
        for serial in samples:
            with self.subTest(serial=serial):
                self.assertTrue(ota.guest_vpkg_service_failed(serial))

    def test_ab_qemu_boot_uses_real_virtio_gpu_and_not_bochs_vga(self) -> None:
        # The previous A/B QEMU commands used the default Q35 Bochs VGA.
        # The guest v-dri check then failed despite a working system.
        class FinishedGuest:
            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "serial.log"
            with patch.object(ota.subprocess, "Popen", return_value=FinishedGuest()) as popen:
                with self.assertRaisesRegex(RuntimeError, "QEMU did not reach"):
                    ota.boot_until(
                        image=Path(temporary) / "unused-disposable.raw",
                        log_path=log,
                        code=None, vars_path=None, qemu="qemu-system-x86_64",
                        qemu_data_dir=None, expected=("expected-marker",), timeout=0,
                    )
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index("-vga") + 1], "none")
            self.assertIn(["-device", "virtio-vga"], [args[i:i+2] for i in range(len(args)-1)])
            self.assertEqual(args[args.index("-display") + 1], "none")

    def test_ignores_other_units_and_expected_package_rejection_diagnostics(self) -> None:
        samples = (
            "vdri-qemu-smoke.service: Failed with result 'exit-code'.",
            "veyra-vpkg-check.service: Failed with result 'exit-code'.",
            "Veyra vpkg VM test: incompatible system-extension level triggered an RPM rollback",
            "Veyra vpkg VM test: scriptlet package rejected without running hooks",
            "VEYRA_VFORGE_CI_FIRST_EXEC_PASS\nVEYRA_VFORGE_CI_SECOND_EXEC_PASS\nVEYRA_VFORGE_CI_DELETE_PASS",
        )
        for serial in samples:
            with self.subTest(serial=serial):
                self.assertFalse(ota.guest_vpkg_service_failed(serial))


if __name__ == "__main__":
    unittest.main()
