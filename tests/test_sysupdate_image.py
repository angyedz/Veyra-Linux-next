from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-sysupdate-image.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_sysupdate_image", SourceFileLoader("veyra_sysupdate_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
sysupdate_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sysupdate_image)

BOOT_SPEC = importlib.util.spec_from_loader(
    "veyra_ab_boot",
    SourceFileLoader("veyra_ab_boot", str(ROOT / "scripts/test-ab-boot.py")),
)
assert BOOT_SPEC and BOOT_SPEC.loader
ab_boot = importlib.util.module_from_spec(BOOT_SPEC)
BOOT_SPEC.loader.exec_module(ab_boot)


class SysupdateImageTests(unittest.TestCase):
    @staticmethod
    def make_test_uki(path: Path) -> None:
        sections = {
            b".cmdline": (b"roothash=" + b"a" * 64 + b" systemd.unit=graphical.target\0", 0x300),
            b".osrel": (b'ID=fedora\nVERSION_ID=44\nIMAGE_VERSION="0.1.0"\n', 0x500),
        }
        image = bytearray(0x700)
        pe_offset = 0x80
        struct.pack_into("<I", image, 0x3C, pe_offset)
        image[pe_offset : pe_offset + 4] = b"PE\0\0"
        struct.pack_into("<H", image, pe_offset + 6, len(sections))
        struct.pack_into("<H", image, pe_offset + 20, 0xF0)
        section_table = pe_offset + 24 + 0xF0
        for index, (name, (content, raw_offset)) in enumerate(sections.items()):
            header = section_table + index * 40
            image[header : header + len(name)] = name
            struct.pack_into("<I", image, header + 8, len(content))
            struct.pack_into("<I", image, header + 16, 0x100)
            struct.pack_into("<I", image, header + 20, raw_offset)
            image[raw_offset : raw_offset + len(content)] = content
        path.write_bytes(image)

    def test_initial_uki_console_patch_preserves_verity_and_image_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uki = Path(temporary) / "initial.efi"
            self.make_test_uki(uki)
            before_osrel = sysupdate_image.os_release_value(uki, "IMAGE_VERSION")

            sysupdate_image.append_uki_cmdline(
                uki,
                "console=ttyS0,115200n8",
                "systemd.show_status=1",
                "systemd.log_target=console",
            )

            sections = sysupdate_image.pe_sections(uki)
            _, cmdline_offset, _ = sections[b".cmdline"]
            with uki.open("rb") as stream:
                stream.seek(cmdline_offset)
                cmdline = stream.read(0x100).split(b"\0", 1)[0].decode("ascii")
            self.assertIn("roothash=" + "a" * 64, cmdline)
            self.assertIn("systemd.unit=graphical.target", cmdline)
            self.assertIn("console=ttyS0,115200n8", cmdline)
            self.assertIn("systemd.show_status=1", cmdline)
            self.assertIn("systemd.log_target=console", cmdline)
            self.assertEqual(sysupdate_image.os_release_value(uki, "IMAGE_VERSION"), before_osrel)

    def test_initial_uki_patch_replaces_existing_key_and_rejects_extra_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uki = Path(temporary) / "initial.efi"
            self.make_test_uki(uki)
            sysupdate_image.append_uki_cmdline(uki, "console=ttyS0,115200n8")
            sysupdate_image.append_uki_cmdline(uki, "console=ttyS1,9600n8")
            with self.assertRaisesRegex(RuntimeError, "simple key=value"):
                sysupdate_image.append_uki_cmdline(uki, "systemd.unit=evil.target;reboot")

            sections = sysupdate_image.pe_sections(uki)
            _, cmdline_offset, _ = sections[b".cmdline"]
            with uki.open("rb") as stream:
                stream.seek(cmdline_offset)
                cmdline = stream.read(0x100).split(b"\0", 1)[0].decode("ascii")
            self.assertEqual(cmdline.count("console="), 1)
            self.assertIn("console=ttyS1,9600n8", cmdline)

    def test_increments_the_last_numeric_version_component(self) -> None:
        self.assertEqual(sysupdate_image.next_version("0.1.9"), "0.1.10")

    def test_rejects_non_numeric_ci_fixture_versions(self) -> None:
        with self.assertRaises(RuntimeError):
            sysupdate_image.next_version("0.1.0-pre-alpha")

    def test_redirects_only_source_urls_for_local_signed_staging(self) -> None:
        transfer_dir = ROOT / "mkosi.extra/usr/lib/sysupdate.d"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            files = base / "serve"
            files.mkdir()
            definitions = sysupdate_image.write_test_definitions(
                files, transfer_dir, "http://127.0.0.1:8765/"
            )
            self.assertEqual(
                {path.name for path in definitions.iterdir()},
                {"10-root-verity.conf", "20-root.conf", "99-uki.conf"},
            )
            for path in definitions.glob("*.conf"):
                content = path.read_text(encoding="utf-8")
                self.assertIn("Path=http://127.0.0.1:8765/", content)
                self.assertIn("Verify=yes", content)
                self.assertNotIn(".raw.zst", content)
                if path.name == "99-uki.conf":
                    self.assertIn("Type=regular-file", content)
                else:
                    self.assertIn(".raw.xz", content)
                    self.assertIn("Type=partition", content)
                    self.assertIn("ReadOnly=1", content)

    def test_uki_fixture_sets_the_verity_hash_boot_target_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.efi"
            destination = Path(temporary) / "update.efi"
            sections = {
                b".cmdline": (b"systemd.unit=graphical.target\0", 0x300),
                b".osrel": (b'ID=fedora\nVERSION_ID=44\nIMAGE_VERSION="0.1.0"\n', 0x400),
            }
            image = bytearray(0x600)
            pe_offset = 0x80
            struct.pack_into("<I", image, 0x3C, pe_offset)
            image[pe_offset : pe_offset + 4] = b"PE\0\0"
            struct.pack_into("<H", image, pe_offset + 6, len(sections))
            struct.pack_into("<H", image, pe_offset + 20, 0xF0)
            section_table = pe_offset + 24 + 0xF0
            for index, (name, (content, raw_offset)) in enumerate(sections.items()):
                header = section_table + index * 40
                image[header : header + len(name)] = name
                struct.pack_into("<I", image, header + 8, len(content))
                struct.pack_into("<I", image, header + 16, 0x100)
                struct.pack_into("<I", image, header + 20, raw_offset)
                image[raw_offset : raw_offset + len(content)] = content
            source.write_bytes(image)

            roothash = "a" * 64
            sysupdate_image.patch_uki(
                source, destination, "0.1.1", roothash, "graphical.target"
            )

            sections_written = sysupdate_image.pe_sections(destination)
            _, cmdline_offset, _ = sections_written[b".cmdline"]
            with destination.open("rb") as stream:
                stream.seek(cmdline_offset)
                cmdline = stream.read(256).split(b"\0", 1)[0].decode("ascii")
            self.assertIn(f"roothash={roothash}", cmdline)
            self.assertIn("veyra.image_version=0.1.1", cmdline)
            self.assertIn("systemd.firstboot=no", cmdline)
            self.assertIn("systemd.unit=graphical.target", cmdline)
            self.assertEqual(sysupdate_image.os_release_value(destination, "VERSION_ID"), "44")
            self.assertEqual(sysupdate_image.os_release_value(destination, "IMAGE_VERSION"), "0.1.1")

    def test_uki_fixture_rejects_a_malformed_verity_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.efi"
            source.write_bytes(b"not a PE image")
            with self.assertRaisesRegex(RuntimeError, "invalid dm-verity root hash"):
                sysupdate_image.patch_uki(source, source.with_name("target.efi"), "0.1.1", "bad", "graphical.target")

    def test_boot_log_normalizer_preserves_systemd_markers_around_terminal_escapes(self) -> None:
        log = (
            "\x1b]3008;start=boot\x1b\\"
            "[\x1b[0;32m  OK  \x1b[0m] Reached target "
            "\x1b[0;1;39mgraphical.target\x1b[0m - Graphical Interface.\r\n"
        )
        self.assertIn("Reached target graphical.target", ab_boot.normalize_log(log))


if __name__ == "__main__":
    unittest.main()
