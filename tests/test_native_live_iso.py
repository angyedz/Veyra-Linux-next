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
        self.assertNotIn("livemedia-creator", SCRIPT)
        self.assertNotIn("lorax-lmc-novirt", SCRIPT)
        self.assertNotIn("lorax-lmc-novirt", WORKFLOW)
        self.assertNotIn("anaconda-tui", WORKFLOW)
    def test_live_system_and_installer(self):
        self.assertIn("plasma-workspace-wayland", SCRIPT)
        self.assertIn("calamares-interactiveterminal", SCRIPT)
        self.assertIn("root=live:CDLABEL=VEYRA_NEXT_LIVE", SCRIPT)
        self.assertIn("source/assets/veyra-recovery.efi", SCRIPT)
        self.assertIn("sudo -n /usr/bin/calamares", SCRIPT)
        self.assertIn("passwd --lock liveuser", SCRIPT)
    def test_live_home_ownership_survives_squashfs(self):
        self.assertIn("live_uid=", SCRIPT)
        self.assertIn("live_gid=", SCRIPT)
        self.assertIn("Live home owner mismatch", SCRIPT)
        self.assertIn("-noappend -comp zstd -no-progress", SCRIPT)
        # Forcing every file to root prevents an autologged-in Plasma user
        # from writing its own home and makes the desktop exit immediately.
        self.assertNotIn("-all-root", SCRIPT)

    def test_vpkg_is_present_and_ota_is_not(self):
        for required in (
            "mkosi.extra/usr/bin/vpkg",
            "vpkg_libdnf.py",
            "veyra_repo.py",
            "live-mode",
            "systemd-sysupdate.service",
            "systemd-sysupdate.timer",
            "systemd-sysupdate-reboot.service",
            "systemd-sysupdate-reboot.timer",
            "chroot \"$root\" /usr/bin/vpkg --help",
            "plasma-discover-flatpak",
        ):
            self.assertIn(required, SCRIPT)
        self.assertIn("Live system must not ship installed OTA updater", SCRIPT)
        self.assertIn("Live system must not contain A/B transfer rules", SCRIPT)
        self.assertNotIn('install -m 0755 "$repo/mkosi.extra/usr/bin/veyra-update"', SCRIPT)
        self.assertNotIn('systemd-bless-boot.service.d/veyra-vpkg-ota.conf', SCRIPT)

    def test_qemu_only_graphics_diagnostics(self):
        for required in (
            "veyra-live-graphics-diagnostics.service",
            "ConditionVirtualization=qemu",
            "/home/liveuser/.local/share/sddm/wayland-session.log",
            "journalctl -b -u sddm.service",
            "VE YRA",
        )[:-1]:
            self.assertIn(required, SCRIPT)

    def test_media_verification(self):
        self.assertIn("8 * 1024 * 1024 * 1024", SCRIPT)
        self.assertIn("BIOS El Torito missing", SCRIPT)
        self.assertIn("UEFI El Torito missing", SCRIPT)
        self.assertIn("sha256sum", SCRIPT)
        self.assertIn("if-no-files-found: error", WORKFLOW)
if __name__ == "__main__":
    unittest.main()
