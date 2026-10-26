from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/lib/veyra/grub_ota.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_grub_ota", SourceFileLoader("veyra_grub_ota", str(SCRIPT))
)
assert SPEC and SPEC.loader
grub_ota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grub_ota)


class GrubOtaTests(unittest.TestCase):
    def test_menu_counts_each_trial_before_loading_kernel_and_keeps_stable_fallback(self) -> None:
        config = grub_ota.render_grub_config("1.0.0", "a" * 64)

        self.assertIn("insmod loadenv", config)
        self.assertIn("load_env --file=/grub2/grubenv", config)
        self.assertIn("set default=veyra-stable", config)
        self.assertIn('set veyra_trial_tries_left=2', config)
        self.assertIn('set veyra_trial_tries_left=1', config)
        self.assertIn('set veyra_trial_tries_left=0', config)
        self.assertIn("save_env veyra_trial_tries_left", config)
        self.assertIn("veyra_trial_version", config)
        self.assertIn("veyra_stable_roothash", config)
        self.assertIn("/VEYRA/veyra-next_${veyra_trial_version}.vmlinuz", config)
        self.assertIn("/VEYRA/veyra-next_${veyra_trial_version}.initrd.zst", config)
        self.assertIn("veyra.image_version=${veyra_trial_version}", config)
        self.assertIn("veyra_stable_version=1.0.0", config)
        self.assertIn("veyra_stable_roothash=" + "a" * 64, config)

    def test_menu_rejects_unsafe_fallback_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid deployment version"):
            grub_ota.render_grub_config("1.0.0; reboot", "a" * 64)

    def test_running_bios_identity_must_match_the_verity_root_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os_release = root / "os-release"
            cmdline = root / "cmdline"
            os_release.write_text(
                'ID=fedora\nVERSION_ID=44\nIMAGE_ID="veyra-next"\n'
                'IMAGE_VERSION="1.2.3"\n',
                encoding="utf-8",
            )
            cmdline.write_text(
                "roothash=" + "a" * 64 + " veyra.image_version=1.2.3\n",
                encoding="ascii",
            )
            self.assertEqual(
                grub_ota.running_bios_identity(os_release, cmdline),
                ("1.2.3", "a" * 64),
            )
            cmdline.write_text(
                "roothash=" + "a" * 64 + " veyra.image_version=1.2.4\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(RuntimeError, "does not match the verity-protected root"):
                grub_ota.running_bios_identity(os_release, cmdline)

    @unittest.skipUnless(
        grub_ota.find_grub_editenv(), "grub-editenv is required to test BIOS boot state"
    )
    def test_fallback_requires_all_trials_and_booting_the_stable_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            boot_dir = Path(temporary)
            esp = boot_dir / "grub2"
            assets = boot_dir / "VEYRA"
            esp.mkdir()
            assets.mkdir()
            config_path = esp / "grub.cfg"
            config_path.write_text(
                grub_ota.render_grub_config("1.0.0", "a" * 64), encoding="ascii"
            )
            env_path = esp / "grubenv"
            grub_ota.initialize_grubenv(env_path, "1.0.0", "a" * 64)
            kernel = assets / "veyra-next_1.1.0.vmlinuz"
            initrd = assets / "veyra-next_1.1.0.initrd.zst"
            kernel.write_bytes(b"kernel")
            initrd.write_bytes(b"initrd")
            grub_ota.stage_trial(
                boot_dir,
                current_version="1.0.0",
                current_roothash="a" * 64,
                target_version="1.1.0",
                target_roothash="b" * 64,
            )

            self.assertFalse(
                grub_ota.finish_fallback(
                    boot_dir,
                    current_version="1.0.0",
                    current_roothash="a" * 64,
                    target_version="1.1.0",
                    target_roothash="b" * 64,
                )
            )
            self.assertTrue(kernel.exists())
            grub_ota.replace_environment(env_path, values={"veyra_trial_tries_left": "0"})
            self.assertTrue(
                grub_ota.finish_fallback(
                    boot_dir,
                    current_version="1.0.0",
                    current_roothash="a" * 64,
                    target_version="1.1.0",
                    target_roothash="b" * 64,
                )
            )
            self.assertFalse(kernel.exists())
            self.assertFalse(initrd.exists())
            self.assertNotIn("veyra_trial_version", grub_ota.read_environment(env_path))

    @unittest.skipUnless(
        grub_ota.find_grub_editenv(), "grub-editenv is required to test BIOS boot state"
    )
    def test_stage_and_bless_move_the_boot_default_only_after_health_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            boot_dir = Path(temporary)
            esp = boot_dir / "grub2"
            assets = boot_dir / "VEYRA"
            esp.mkdir()
            assets.mkdir()
            config_path = esp / "grub.cfg"
            config_path.write_text(
                grub_ota.render_grub_config("1.0.0", "a" * 64), encoding="ascii"
            )
            env_path = esp / "grubenv"
            grub_ota.initialize_grubenv(env_path, "1.0.0", "a" * 64)
            (assets / "veyra-next_1.1.0.vmlinuz").write_bytes(b"kernel")
            (assets / "veyra-next_1.1.0.initrd.zst").write_bytes(b"initrd")

            grub_ota.stage_trial(
                boot_dir,
                current_version="1.0.0",
                current_roothash="a" * 64,
                target_version="1.1.0",
                target_roothash="b" * 64,
            )
            staged = grub_ota.read_environment(env_path)
            self.assertEqual(staged["veyra_stable_version"], "1.0.0")
            self.assertEqual(staged["veyra_trial_version"], "1.1.0")
            self.assertEqual(staged["veyra_trial_tries_left"], "3")

            with self.assertRaisesRegex(RuntimeError, "does not match the pending GRUB trial"):
                grub_ota.bless_trial(boot_dir, "1.1.0", "c" * 64)

            grub_ota.bless_trial(boot_dir, "1.1.0", "b" * 64)
            blessed = grub_ota.read_environment(env_path)
            self.assertEqual(blessed["veyra_stable_version"], "1.1.0")
            self.assertEqual(blessed["veyra_stable_roothash"], "b" * 64)
            self.assertNotIn("veyra_trial_version", blessed)
            self.assertIn("veyra_stable_version=1.1.0", config_path.read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
