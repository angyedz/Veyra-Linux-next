#!/usr/bin/env python3
"""Prepare mkosi's verity partitions and UKI for a Veyra sysupdate release."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
EFI_SYSTEM_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
sys.path.insert(0, str(ROOT / "mkosi.extra/usr/lib/veyra"))
import grub_ota


def image_version(output_dir: pathlib.Path | None = None) -> str:
    os_release = output_dir / "veyra-next.osrelease" if output_dir else None
    if os_release is not None and os_release.is_file():
        versions = []
        for raw_line in os_release.read_text(encoding="utf-8").splitlines():
            key, separator, value = raw_line.partition("=")
            if separator and key == "IMAGE_VERSION":
                versions.append(value.strip().strip('"'))
        if len(versions) != 1 or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]*", versions[0]
        ):
            raise ValueError(f"invalid or ambiguous IMAGE_VERSION in {os_release}")
        return versions[0]

    section = ""
    for raw_line in (ROOT / "mkosi.conf").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Output" and line.startswith("ImageVersion="):
            version = line.partition("=")[2].strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
                raise ValueError(f"unsafe ImageVersion for update artifact names: {version!r}")
            return version
    raise ValueError("mkosi.conf has no ImageVersion in [Output]")


def extract_uki_section(uki: pathlib.Path, section_name: bytes, destination: pathlib.Path) -> None:
    with uki.open("rb") as stream:
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"invalid UKI PE signature: {uki}")
        stream.seek(pe_offset + 6)
        section_count = struct.unpack("<H", stream.read(2))[0]
        stream.seek(pe_offset + 20)
        optional_header_size = struct.unpack("<H", stream.read(2))[0]
        section_table = pe_offset + 24 + optional_header_size
        for index in range(section_count):
            header = section_table + index * 40
            stream.seek(header)
            name = stream.read(8).split(b"\0", 1)[0]
            stream.seek(header + 16)
            raw_size, raw_offset = struct.unpack("<II", stream.read(8))
            if name == section_name:
                if raw_size == 0:
                    raise ValueError(f"UKI section {section_name!r} is empty")
                stream.seek(raw_offset)
                data = stream.read(raw_size)
                if len(data) != raw_size:
                    raise ValueError(f"truncated UKI section {section_name!r}")
                with destination.open("wb") as output:
                    output.write(data)
                return
    raise ValueError(f"UKI has no {section_name.decode('ascii', errors='replace')} section")


def esp_offset(image: pathlib.Path) -> int:
    table = json.loads(
        subprocess.check_output(["sfdisk", "--json", str(image)], text=True)
    )["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    partitions = [
        partition
        for partition in table["partitions"]
        if str(partition.get("type", "")).lower() == EFI_SYSTEM_TYPE
    ]
    if len(partitions) != 1:
        raise ValueError(f"expected exactly one EFI System Partition, found {len(partitions)}")
    return int(partitions[0]["start"]) * sector_size


def write_bios_boot_files(
    image: pathlib.Path,
    output_dir: pathlib.Path,
    version: str,
    roothash: str,
) -> None:
    kernel = output_dir / f"veyra-next_{version}.vmlinuz"
    initrd = output_dir / f"veyra-next_{version}.initrd.zst"
    source_initrd = output_dir / "initrd.cpio.zst"
    if not kernel.is_file() or not initrd.is_file() or not source_initrd.is_file():
        raise ValueError("missing versioned BIOS kernel/initramfs artifacts")

    config = grub_ota.render_grub_config(version, roothash)
    with tempfile.TemporaryDirectory(prefix="veyra-grub-config-") as temporary:
        temporary_path = pathlib.Path(temporary)
        config_path = temporary_path / "grub.cfg"
        grubenv_path = temporary_path / "grubenv"
        config_path.write_text(config, encoding="ascii")
        grub_ota.initialize_grubenv(grubenv_path, version, roothash)
        offset = esp_offset(image)
        image_ref = f"{image}@@{offset}"
        for directory in ("::/VEYRA", "::/grub2"):
            result = subprocess.run(
                ["mdir", "-i", image_ref, directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                subprocess.run(["mmd", "-i", image_ref, directory], check=True)
        subprocess.run(
            ["mcopy", "-o", "-i", image_ref, str(kernel), f"::/VEYRA/{kernel.name}"],
            check=True,
        )
        subprocess.run(
            ["mcopy", "-o", "-i", image_ref, str(initrd), f"::/VEYRA/{initrd.name}"],
            check=True,
        )
        subprocess.run(
            ["mcopy", "-o", "-i", image_ref, str(config_path), "::/grub2/grub.cfg"],
            check=True,
        )
        subprocess.run(
            ["mcopy", "-o", "-i", image_ref, str(grubenv_path), "::/grub2/grubenv"],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    version = image_version(output_dir)
    versioned_uki = output_dir / f"veyra-next_{version}.efi"
    versioned_kernel = output_dir / f"veyra-next_{version}.vmlinuz"
    versioned_initrd = output_dir / f"veyra-next_{version}.initrd.zst"
    for stale in output_dir.iterdir():
        if not stale.is_file():
            continue
        if stale.name.endswith((".root.raw.zst", ".root-verity.raw.zst")):
            stale.unlink()
        elif stale.name.startswith("veyra-next_") and stale.name.endswith(".efi") and stale != versioned_uki:
            stale.unlink()
        elif stale.name.startswith("veyra-next_") and stale.suffix in {".vmlinuz", ".zst"} and stale not in {
            versioned_kernel,
            versioned_initrd,
        }:
            stale.unlink()

    uki = output_dir / "veyra-next.efi"
    if not uki.is_file() or uki.stat().st_size == 0:
        print(f"missing mkosi UKI output: {uki}", file=sys.stderr)
        return 1

    image = output_dir / "veyra-next.raw"
    initrd = output_dir / "initrd.cpio.zst"
    if not image.is_file() or not initrd.is_file():
        print("missing disk image or mkosi initrd output for Legacy BIOS boot", file=sys.stderr)
        return 1

    artifact_sets = {"root": ".root.raw", "root verity": ".root-verity.raw"}
    normalized: dict[str, pathlib.Path] = {}
    partition_uuids: dict[str, str] = {}
    for kind, extension in artifact_sets.items():
        artifacts = sorted(
            path for path in output_dir.iterdir() if path.is_file() and path.name.endswith(extension)
        )
        if len(artifacts) != 1 or artifacts[0].stat().st_size == 0:
            available = ", ".join(sorted(path.name for path in output_dir.iterdir()))
            print(
                f"expected one non-empty {kind} split artifact, found {len(artifacts)}; "
                f"available outputs: {available}",
                file=sys.stderr,
            )
            return 1

        artifact = artifacts[0]
        match = re.fullmatch(
            rf"veyra-next\._{re.escape(version)}_([0-9a-fA-F-]+){re.escape(extension)}",
            artifact.name,
        )
        if not match:
            print(f"unexpected {kind} split artifact name: {artifact.name}", file=sys.stderr)
            return 1

        partition_uuids[kind] = match.group(1).replace("-", "").lower()

        # systemd-sysupdate's URL source pattern is `veyra-next_@v_@u...`.
        # mkosi inserts a dot before SplitName, so normalize that one boundary.
        normalized_path = output_dir / f"veyra-next_{version}_{match.group(1)}{extension}"
        artifact.rename(normalized_path)
        normalized[kind] = normalized_path

    roothash_path = output_dir / "veyra-next.roothash"
    if not roothash_path.is_file():
        print(f"missing mkosi root hash artifact: {roothash_path}", file=sys.stderr)
        return 1
    roothash = roothash_path.read_text(encoding="ascii").strip().lower()
    if partition_uuids["root"] + partition_uuids["root verity"] != roothash:
        print("root and verity split artifact UUIDs do not encode the generated dm-verity root hash", file=sys.stderr)
        return 1

    extract_uki_section(uki, b".linux", versioned_kernel)
    shutil.copy2(initrd, versioned_initrd)
    shutil.copy2(uki, versioned_uki)
    for artifact in normalized.values():
        subprocess.run(["zstd", "--rm", "-T0", "-9", str(artifact)], check=True)

    roothash = roothash_path.read_text(encoding="ascii").strip().lower()
    write_bios_boot_files(image, output_dir, version, roothash)

    print(
        f"Prepared Veyra {version} root, verity, UKI, and Legacy BIOS kernel/initrd "
        f"artifacts in {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
