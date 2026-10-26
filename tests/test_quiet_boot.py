"""Source-level boot presentation contract, not a QEMU boot test."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QuietBootTests(unittest.TestCase):
    def test_systemd_boot_retained(self):
        config = (ROOT / "mkosi.conf").read_text()
        self.assertIn("Bootloader=systemd-boot", config)
        self.assertIn("UnifiedKernelImages=unsigned", config)

    def test_quiet_but_errors_remain_visible(self):
        config = (ROOT / "mkosi.conf").read_text()
        line = next(x for x in config.splitlines() if x.startswith("KernelCommandLine="))
        args = line.partition("=")[2].split()
        for token in ("quiet", "splash", "loglevel=3", "systemd.show_status=auto"):
            self.assertIn(token, args)
        self.assertNotIn("systemd.show_status=no", args)
        self.assertNotIn("loglevel=0", args)

    def test_plymouth_dependencies(self):
        config = (ROOT / "mkosi.conf").read_text()
        self.assertIn("Packages=plymouth\n", config)
        self.assertIn("Packages=plymouth-plugin-script\n", config)

    def test_theme_files_exist(self):
        theme = ROOT / "mkosi.extra/usr/share/plymouth/themes/veyra"
        descriptor = (theme / "veyra.plymouth").read_text()
        script = (theme / "veyra.script").read_text()
        self.assertIn("ModuleName=script", descriptor)
        self.assertIn("ScriptFile=/usr/share/plymouth/themes/veyra/veyra.script", descriptor)
        self.assertIn("Window.SetBackgroundTopColor", script)
        self.assertIn('Image.Text("VEYRA"', script)

    def test_theme_selected_without_host_initrd_rebuild(self):
        script = (ROOT / "mkosi.postinst.chroot").read_text()
        self.assertIn("plymouth-set-default-theme veyra", script)
        self.assertNotIn("plymouth-set-default-theme -R", script)
        self.assertNotIn("dracut --force", script)


if __name__ == "__main__":
    unittest.main()
