"""Ensure the QEMU boot gate refuses firmware-only and screenshot-only success."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("iso_boot_gate", ROOT / "scripts/verify-live-iso-qemu.py")
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def good_report():
    return {
        "cdrom_readonly": True,
        "network_disabled": True,
        "efi_variables_temporary": True,
        "modes": [
            {
                "mode": mode,
                "errors": [],
                "screenshots": [{"file": "screen-120.png"}],
                "serial_signals": {
                    "kernel_seen": True,
                    "init_seen": True,
                    "live_desktop_confirmed": False,
                    "plasma_runtime_confirmed": True,
                    "plasma_runtime_failed": False,
                    "vpkg_guest_test_passed": True,
                    "vpkg_guest_test_failed": False,
                },
            }
            for mode in ("bios", "uefi")
        ],
    }


class FirmwareBootGateTests(unittest.TestCase):
    def test_kernel_and_init_can_pass_without_claiming_desktop(self):
        self.assertEqual(module.verify(good_report()), [])

    def test_missing_kernel_or_init_must_fail(self):
        for missing in ("kernel_seen", "init_seen"):
            report = good_report()
            report["modes"][0]["serial_signals"][missing] = False
            with self.subTest(missing=missing):
                self.assertTrue(module.verify(report))

    def test_desktop_process_probe_is_required(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["plasma_runtime_confirmed"] = False
        report["modes"][1]["serial_signals"]["plasma_runtime_failed"] = True
        failures = module.verify(report)
        self.assertTrue(any("plasmashell" in failure for failure in failures))
        self.assertTrue(any("process probe" in failure for failure in failures))

    def test_fully_black_final_screenshot_must_fail(self):
        report = good_report()
        report["modes"][0]["screenshots"][-1]["non_black_pixels_detected"] = False
        failures = module.verify(report)
        self.assertTrue(any("completely black" in failure for failure in failures))

    def test_qemu_failure_or_unchecked_firmware_fails(self):
        report = good_report()
        report["modes"][1]["errors"].append("QEMU exited 1")
        self.assertTrue(module.verify(report))
        report = good_report()
        report["modes"].pop()
        self.assertTrue(module.verify(report))

    def test_plasma_crash_or_pam_failure_must_fail(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["plasma_session_crashed"] = True
        report["modes"][1]["serial_signals"]["pam_systemd_missing"] = True
        failures = module.verify(report)
        self.assertTrue(any("crashed Plasma" in failure for failure in failures))
        self.assertTrue(any("systemd-pam" in failure for failure in failures))

    def test_missing_images_and_unsafe_configuration_fail(self):
        report = good_report()
        report["modes"][0]["screenshots"] = []
        report["network_disabled"] = False
        self.assertGreaterEqual(len(module.verify(report)), 2)


if __name__ == "__main__":
    unittest.main()
