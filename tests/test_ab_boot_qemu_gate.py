from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import unittest


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
