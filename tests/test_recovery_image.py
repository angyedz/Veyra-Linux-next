from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_recovery_uki", ROOT / "scripts" / "install_recovery_uki.py"
)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class RecoveryUkiInstallTests(unittest.TestCase):
    def test_fedora44_recovery_is_a_standalone_uki_subimage(self) -> None:
        main_config = (ROOT / "mkosi.conf").read_text(encoding="utf-8")
        recovery_config = (ROOT / "mkosi.images/recovery/mkosi.conf").read_text(encoding="utf-8")
        init_script = ROOT / "mkosi.images/recovery/mkosi.extra/init"
        recovery_doc = (ROOT / "docs/recovery.md").read_text(encoding="utf-8")

        self.assertIn("Release=44", main_config)
        self.assertNotIn("Distribution=", recovery_config)
        self.assertNotIn("Release=", recovery_config)
        self.assertIn("Format=uki", recovery_config)
        self.assertIn("ImageId=veyra-recovery", recovery_config)
        self.assertIn("rdinit=/init", recovery_config)
        self.assertTrue(init_script.is_file())
        self.assertIn("Initrds=%O/initrd", main_config)
        self.assertRegex(main_config, r"(?m)^Dependencies=initrd recovery$")
        self.assertIn("not mount or execute the installed Veyra root", recovery_doc)

        finalizer = (ROOT / "mkosi.finalize").read_text(encoding="utf-8")
        self.assertIn('--output-dir "$OUTPUTDIR"', finalizer)

    def test_installs_single_recovery_uki_into_esp_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "root"
            outputs = pathlib.Path(temporary) / "outputs"
            outputs.mkdir()
            artifact = outputs / "veyra-recovery_0.1.0.efi"
            artifact.write_bytes(b"test UKI")

            destination = installer.install_recovery_uki(root, outputs)

            self.assertEqual(destination, root / "efi/EFI/Linux/veyra-recovery.efi")
            self.assertEqual(destination.read_bytes(), b"test UKI")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)

    def test_fails_when_recovery_uki_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(installer.RecoveryImageError, "exactly one"):
                installer.install_recovery_uki(
                    pathlib.Path(temporary) / "root", pathlib.Path(temporary) / "outputs"
                )

    def test_fails_closed_when_recovery_uki_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outputs = pathlib.Path(temporary) / "outputs"
            outputs.mkdir()
            (outputs / "veyra-recovery_0.1.0.efi").write_bytes(b"first")
            (outputs / "veyra-recovery_0.2.0.efi").write_bytes(b"second")
            with self.assertRaisesRegex(installer.RecoveryImageError, "exactly one"):
                installer.install_recovery_uki(pathlib.Path(temporary) / "root", outputs)

    def test_refuses_a_symlink_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outputs = pathlib.Path(temporary) / "outputs"
            outputs.mkdir()
            target = pathlib.Path(temporary) / "outside.efi"
            target.write_bytes(b"not an artifact")
            (outputs / "veyra-recovery_0.1.0.efi").symlink_to(target)
            with self.assertRaisesRegex(installer.RecoveryImageError, "regular file"):
                installer.install_recovery_uki(pathlib.Path(temporary) / "root", outputs)


if __name__ == "__main__":
    unittest.main()
