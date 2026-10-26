from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-update-artifacts.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_prepare_update_artifacts", SourceFileLoader("veyra_prepare_update_artifacts", str(SCRIPT))
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def write_uki(path: Path, section_name: bytes, section: bytes) -> None:
    pe_offset = 0x80
    section_table = pe_offset + 24
    section_offset = 0x200
    image = bytearray(section_offset + len(section))
    struct.pack_into("<I", image, 0x3C, pe_offset)
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<H", image, pe_offset + 6, 1)
    struct.pack_into("<H", image, pe_offset + 20, 0)
    image[section_table : section_table + len(section_name)] = section_name
    struct.pack_into("<I", image, section_table + 16, len(section))
    struct.pack_into("<I", image, section_table + 20, section_offset)
    image[section_offset:] = section
    path.write_bytes(image)


class BiosArtifactTests(unittest.TestCase):
    def test_uses_the_built_image_version_when_mkosi_overrides_the_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "veyra-next.osrelease").write_text(
                'IMAGE_ID="veyra-next"\nIMAGE_VERSION="0.1.1"\n', encoding="utf-8"
            )

            self.assertEqual(prepare.image_version(output), "0.1.1")

    def test_rejects_ambiguous_built_image_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "veyra-next.osrelease").write_text(
                'IMAGE_VERSION="0.1.1"\nIMAGE_VERSION="0.1.2"\n', encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "invalid or ambiguous IMAGE_VERSION"):
                prepare.image_version(output)

    def test_extracts_exact_linux_section_from_uki(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uki = root / "source.efi"
            output = root / "kernel"
            write_uki(uki, b".linux", b"kernel-payload")

            prepare.extract_uki_section(uki, b".linux", output)

            self.assertEqual(output.read_bytes(), b"kernel-payload")

    def test_rejects_uki_without_requested_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uki = root / "source.efi"
            write_uki(uki, b".osrel", b"ID=fedora\n")

            with self.assertRaisesRegex(ValueError, "has no .linux section"):
                prepare.extract_uki_section(uki, b".linux", root / "kernel")

    def test_writes_versioned_grub_entry_that_pairs_kernel_and_roothash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "disk.raw"
            image.touch()
            output = root / "output"
            output.mkdir()
            (output / "veyra-next_0.1.0.vmlinuz").write_bytes(b"kernel")
            (output / "veyra-next_0.1.0.initrd.zst").write_bytes(b"initrd")
            (output / "initrd.cpio.zst").write_bytes(b"source initrd")
            copied_config: list[str] = []
            copied_grubenv: list[bytes] = []

            def capture_copy(command: list[str], **_kwargs: object) -> object:
                if command[0] == "mcopy" and command[-1] == "::/grub2/grub.cfg":
                    copied_config.append(Path(command[-2]).read_text(encoding="ascii"))
                if command[0] == "mcopy" and command[-1] == "::/grub2/grubenv":
                    copied_grubenv.append(Path(command[-2]).read_bytes())
                return type("Completed", (), {"returncode": 0})()

            def create_env(path: Path, version: str, roothash: str) -> None:
                self.assertEqual((version, roothash), ("0.1.0", "a" * 64))
                path.write_bytes(b"initial GRUB state")

            with (
                patch.object(prepare, "esp_offset", return_value=4096),
                patch.object(prepare.grub_ota, "initialize_grubenv", side_effect=create_env),
                patch.object(prepare.subprocess, "run", side_effect=capture_copy) as run,
            ):
                prepare.write_bios_boot_files(
                    image,
                    output,
                    "0.1.0",
                    "a" * 64,
                )

            self.assertEqual(len(copied_config), 1)
            self.assertEqual(copied_grubenv, [b"initial GRUB state"])
            self.assertIn("set default=veyra-stable", copied_config[0])
            self.assertIn("insmod loadenv", copied_config[0])
            self.assertIn("load_env --file=/grub2/grubenv", copied_config[0])
            self.assertIn("roothash=" + "a" * 64, copied_config[0])
            self.assertIn("/VEYRA/veyra-next_${veyra_stable_version}.vmlinuz", copied_config[0])
            self.assertIn("/VEYRA/veyra-next_${veyra_stable_version}.initrd.zst", copied_config[0])
            self.assertIn("veyra.image_version=${veyra_stable_version}", copied_config[0])
            self.assertEqual(run.call_count, 6)


if __name__ == "__main__":
    unittest.main()
