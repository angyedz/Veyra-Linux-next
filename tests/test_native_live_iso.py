"""No-Anaconda media composition contract checks; no privileged operations."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/build-native-live-iso.sh").read_text()
WORKFLOW = (ROOT / ".github/workflows/native-live-iso.yml").read_text()

class NativeLiveIsoTests(unittest.TestCase):
    def test_toolchain(self):
        self.assertIn("dnf5 -y --installroot=", SCRIPT)
        self.assertIn("mksquashfs", SCRIPT)
        self.assertIn("grub2-mkrescue", SCRIPT)
        self.assertIn("--add \"dmsquash-live\"", SCRIPT)
        self.assertNotIn("livemedia-creator", SCRIPT)
        self.assertNotIn("lorax", SCRIPT.lower())
        self.assertNotIn("lorax-lmc-novirt", WORKFLOW)
        self.assertNotIn("anaconda-tui", WORKFLOW)
    def test_live_system_and_installer(self):
        self.assertIn("plasma-workspace-wayland", SCRIPT)
        self.assertIn("calamares-interactiveterminal", SCRIPT)
        self.assertIn("root=live:CDLABEL=VEYRA_NEXT_LIVE", SCRIPT)
        self.assertIn("source/assets/veyra-recovery.efi", SCRIPT)
        self.assertIn("sudo -n /usr/bin/calamares", SCRIPT)
        self.assertIn("passwd --lock liveuser", SCRIPT)
    def test_media_verification(self):
        self.assertIn("8 * 1024 * 1024 * 1024", SCRIPT)
        self.assertIn("BIOS El Torito missing", SCRIPT)
        self.assertIn("UEFI El Torito missing", SCRIPT)
        self.assertIn("sha256sum", SCRIPT)
        self.assertIn("if-no-files-found: error", WORKFLOW)
if __name__ == "__main__":
    unittest.main()
