"""No-Anaconda media composition contract checks; no privileged operations."""
import pathlib
import subprocess
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
        self.assertIn("Exec=/usr/libexec/veyra-live-launch-calamares", SCRIPT)
        self.assertIn("passwd --lock liveuser", SCRIPT)
    def test_passwordless_live_vpkg_is_narrow_and_disposable(self):
        self.assertIn("root:veyra", SCRIPT)
        self.assertIn("PermitRootLogin no", SCRIPT)
        self.assertIn("PasswordAuthentication no", SCRIPT)
        self.assertIn("veyra-live-vpkg-install", SCRIPT)
        self.assertIn("visudo -cf /etc/sudoers.d/veyra-live-vpkg-install", SCRIPT)
        self.assertIn("NOPASSWD: /usr/bin/vpkg ^install", SCRIPT)
        self.assertIn('exec /usr/bin/sudo -n /usr/bin/vpkg install "$@"', SCRIPT)
        self.assertIn('exec /usr/bin/vpkg "$@"', SCRIPT)
        self.assertNotIn("NOPASSWD: ALL", SCRIPT)
        self.assertNotIn("useradd --create-home --groups wheel", SCRIPT)

    def test_live_session_never_requires_unlock_password_or_sleeps(self):
        self.assertIn("kscreenlockerrc", SCRIPT)
        self.assertIn("Autolock=false", SCRIPT)
        self.assertIn("LockOnResume=false", SCRIPT)
        self.assertIn("RequirePassword=false", SCRIPT)
        self.assertIn("chown -R liveuser:liveuser /home/liveuser/.config", SCRIPT)
        for required in (
            "sleep.target", "suspend.target", "hibernate.target",
            "hybrid-sleep.target", "suspend-then-hibernate.target",
            "IdleAction=ignore", "HandleSuspendKey=ignore",
        ):
            self.assertIn(required, SCRIPT)

    def test_calamares_is_desktop_visible_and_wayland_user_gui(self):
        self.assertIn("konsole konsole-part", SCRIPT)
        self.assertIn("konsolepart.so", SCRIPT)
        self.assertIn("veyra-live-launch-calamares", SCRIPT)
        self.assertIn("Install Veyra Next.desktop", SCRIPT)
        self.assertIn('chown -R liveuser:liveuser /home/liveuser/Desktop', SCRIPT)
        self.assertIn('Exec=/usr/libexec/veyra-live-launch-calamares', SCRIPT)
        self.assertIn("StartupNotify=true", SCRIPT)
        self.assertIn('/usr/bin/calamares -c /etc/calamares', SCRIPT)
        self.assertIn("kdialog --title", SCRIPT)
        self.assertNotIn("Exec=sudo -n /usr/bin/calamares", SCRIPT)
        self.assertNotIn("NOPASSWD: /usr/bin/calamares", SCRIPT)

    def test_calamares_backend_cannot_skip_recovery_preflight(self):
        self.assertIn('NOPASSWD: /usr/libexec/veyra-installer ""', SCRIPT)
        self.assertIn('/usr/libexec/veyra-installer --preflight', SCRIPT)
        self.assertIn('visudo -cf /etc/sudoers.d/veyra-live-calamares', SCRIPT)
        self.assertIn("Installation is disabled:", SCRIPT)
        self.assertIn("No disk has been modified.", SCRIPT)
        self.assertIn('if ! /usr/bin/sudo -n /usr/libexec/veyra-installer --preflight;', SCRIPT)
        self.assertNotIn("NOPASSWD: /usr/libexec/veyra-installer ^", SCRIPT)
        modules = (ROOT / "installer/calamares/modules/interactiveterminal.conf").read_text()
        settings = (ROOT / "installer/calamares/settings.conf").read_text()
        self.assertIn("command: /usr/libexec/veyra-live-calamares-session", modules)
        self.assertIn("- interactiveterminal", settings)
        self.assertNotIn("- finished", settings)

    def test_installer_embedded_scripts_are_valid_bash(self):
        for path in ("/usr/libexec/veyra-live-launch-calamares",
                     "/usr/libexec/veyra-live-calamares-session"):
            marker = f'cat > "$root{path}" <<\'EOF\'\n'
            self.assertIn(marker, SCRIPT)
            section = SCRIPT.split(marker, 1)[1].split("\nEOF", 1)[0]
            check = subprocess.run(["bash", "-n"], text=True,
                                   input=section + "\n", capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_wayland_session_runtime_dependencies(self):
        self.assertIn("systemd-pam dbus dbus-daemon", SCRIPT)
        for required in (
            "/usr/bin/dbus-run-session",
            "/usr/bin/dbus-daemon",
            "/usr/bin/startplasma-wayland",
            "/usr/libexec/plasma-dbus-run-session-if-needed",
            "/usr/lib64/security/pam_systemd.so",
        ):
            self.assertIn(required, SCRIPT)
        self.assertIn('chroot "$root" /usr/bin/dbus-run-session --version', SCRIPT)

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

    def test_virtio_workaround_is_qemu_live_only(self):
        self.assertIn("90-veyra-live-virtio-render", SCRIPT)
        self.assertIn("systemd-detect-virt --vm", SCRIPT)
        self.assertIn('"virtio_gpu"', SCRIPT)
        self.assertIn("KWIN_RENDER_NODES=", SCRIPT)
        self.assertIn("/etc/veyra/live-mode", SCRIPT)

    def test_qemu_only_graphics_diagnostics(self):
        for required in (
            "veyra-live-graphics-diagnostics.service",
            "ConditionVirtualization=qemu",
            "/home/liveuser/.local/share/sddm/wayland-session.log",
            "journalctl -b -u sddm.service",
            "VEYRA LIVE GRAPHICS DIAGNOSTICS",
        ):
            self.assertIn(required, SCRIPT)

    def test_media_verification(self):
        self.assertIn("8 * 1024 * 1024 * 1024", SCRIPT)
        self.assertIn("BIOS El Torito missing", SCRIPT)
        self.assertIn("UEFI El Torito missing", SCRIPT)
        self.assertIn("sha256sum", SCRIPT)
        self.assertIn("if-no-files-found: error", WORKFLOW)
if __name__ == "__main__":
    unittest.main()
