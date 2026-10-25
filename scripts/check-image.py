#!/usr/bin/env python3
"""Check the GPT layout of a built Veyra A/B image."""

from __future__ import annotations

import json
import pathlib
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT_TYPE = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
ROOT_VERITY_TYPE = "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"
EXPECTED_FEDORA_RELEASE = "44"


def os_release_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key in fields:
            raise ValueError(f"duplicate os-release key: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    return fields


def uki_os_release(path: pathlib.Path) -> dict[str, str]:
    with path.open("rb") as stream:
        stream.seek(0x3C)
        pe_offset_raw = stream.read(4)
        if len(pe_offset_raw) != 4:
            raise ValueError(f"UKI has no PE header: {path}")
        pe_offset = struct.unpack("<I", pe_offset_raw)[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"UKI has an invalid PE signature: {path}")
        stream.seek(pe_offset + 6)
        section_count_raw = stream.read(2)
        stream.seek(pe_offset + 20)
        optional_size_raw = stream.read(2)
        if len(section_count_raw) != 2 or len(optional_size_raw) != 2:
            raise ValueError(f"UKI has a truncated PE header: {path}")
        section_count = struct.unpack("<H", section_count_raw)[0]
        optional_size = struct.unpack("<H", optional_size_raw)[0]
        section_table = pe_offset + 24 + optional_size
        osrel_section: tuple[int, int] | None = None
        for index in range(section_count):
            header = section_table + 40 * index
            stream.seek(header)
            name = stream.read(8).split(b"\0", 1)[0]
            stream.seek(header + 16)
            raw_size, raw_offset = struct.unpack("<II", stream.read(8))
            if name == b".osrel":
                osrel_section = (raw_offset, raw_size)
                break
        if osrel_section is None:
            raise ValueError(f"UKI has no .osrel section: {path}")
        raw_offset, raw_size = osrel_section
        stream.seek(raw_offset)
        raw_osrel = stream.read(raw_size).split(b"\0", 1)[0]
    try:
        return os_release_fields(raw_osrel.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ValueError(f"UKI has a non-text .osrel section: {path}") from error


def validate_fedora_release(image: pathlib.Path) -> None:
    osrelease_file = image.with_name(f"{image.stem}.osrelease")
    uki_file = image.with_name(f"{image.stem}.efi")
    manifest_file = image.with_name(f"{image.stem}.manifest")
    for path in (osrelease_file, uki_file, manifest_file):
        if not path.is_file():
            raise ValueError(f"missing Fedora release validation artifact: {path}")

    root_release = os_release_fields(osrelease_file.read_text(encoding="utf-8"))
    uki_release = uki_os_release(uki_file)
    for label, fields in (("root", root_release), ("UKI", uki_release)):
        if fields.get("ID") != "fedora" or fields.get("VERSION_ID") != EXPECTED_FEDORA_RELEASE:
            raise ValueError(
                f"{label} artifact must identify Fedora {EXPECTED_FEDORA_RELEASE}; "
                f"found ID={fields.get('ID')!r}, VERSION_ID={fields.get('VERSION_ID')!r}"
            )
    for key in ("IMAGE_ID", "IMAGE_VERSION"):
        if root_release.get(key) != uki_release.get(key):
            raise ValueError(f"root and UKI disagree on {key}")
    if root_release.get("IMAGE_ID") != "veyra-next":
        raise ValueError("root and UKI must identify the Veyra image")

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    packages = manifest.get("packages") if isinstance(manifest, dict) else None
    if not isinstance(packages, list):
        raise ValueError(f"invalid package manifest: {manifest_file}")
    by_name = {
        package.get("name"): package
        for package in packages
        if isinstance(package, dict) and isinstance(package.get("name"), str)
    }
    expected = ("fedora-release", "systemd", "systemd-udev", "kernel-core")
    missing = [name for name in expected if name not in by_name]
    if missing:
        raise ValueError("package manifest is missing Fedora base RPMs: " + ", ".join(missing))

    fedora_version = str(by_name["fedora-release"].get("version", ""))
    if not fedora_version.startswith(f"{EXPECTED_FEDORA_RELEASE}-"):
        raise ValueError(
            f"fedora-release RPM must be version {EXPECTED_FEDORA_RELEASE}; found {fedora_version!r}"
        )
    for name in ("systemd", "systemd-udev", "kernel-core"):
        version = str(by_name[name].get("version", ""))
        if not version.endswith(".fc44"):
            raise ValueError(f"{name} RPM must come from Fedora 44; found {version!r}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-image.py IMAGE.raw", file=sys.stderr)
        return 2

    image = pathlib.Path(sys.argv[1])
    if not image.is_file() or image.stat().st_size == 0:
        print(f"missing disk image: {image}", file=sys.stderr)
        return 1

    # Keep the update target a plain GPT disk image. An El Torito ISO9660
    # descriptor at sector 16 makes systemd-dissection treat the whole disk as
    # an ISO filesystem before it can select the root and verity partitions.
    with image.open("rb") as disk:
        disk.seek(16 * 2048 + 1)
        if disk.read(5) == b"CD001":
            print("disk image must not contain an El Torito ISO9660 descriptor", file=sys.stderr)
            return 1

    try:
        layout = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)], text=True))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"could not read GPT layout from {image}: {error}", file=sys.stderr)
        return 1

    partitions = layout.get("partitiontable", {}).get("partitions", [])
    root_partitions = [p for p in partitions if p.get("type", "").lower() == ROOT_TYPE]
    verity_partitions = [p for p in partitions if p.get("type", "").lower() == ROOT_VERITY_TYPE]
    labels = {p.get("name", "") for p in partitions}
    osrelease_file = image.with_name(f"{image.stem}.osrelease")
    if not osrelease_file.is_file():
        print(f"missing mkosi os-release artifact: {osrelease_file}", file=sys.stderr)
        return 1
    image_id = ""
    image_version = ""
    for line in osrelease_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"IMAGE_ID", "IMAGE_VERSION"}:
            value = value.strip().strip('"')
            if key == "IMAGE_ID":
                image_id = value
            else:
                image_version = value
    if image_id != "veyra-next" or not image_version:
        print(f"unexpected image identity in {osrelease_file}", file=sys.stderr)
        return 1
    try:
        validate_fedora_release(image)
    except (OSError, ValueError, json.JSONDecodeError, struct.error) as error:
        print(f"Fedora 44 image gate failed: {error}", file=sys.stderr)
        return 1

    active_root_label = f"{image_id}_{image_version}"
    active_verity_label = f"{active_root_label}_verity"
    expected_labels = {active_root_label, active_verity_label, "VEYRA_ETC", "VEYRA_VAR"}

    if len(root_partitions) != 2:
        print(f"expected two root partitions for A/B, found {len(root_partitions)}", file=sys.stderr)
        return 1
    if len(verity_partitions) != 2:
        print(f"expected two root verity partitions for A/B, found {len(verity_partitions)}", file=sys.stderr)
        return 1
    if not expected_labels.issubset(labels):
        missing = ", ".join(sorted(expected_labels - labels))
        print(f"missing GPT partition labels: {missing}", file=sys.stderr)
        return 1

    active_roots = [p for p in root_partitions if p.get("name") == active_root_label]
    active_verities = [p for p in verity_partitions if p.get("name") == active_verity_label]
    if len(active_roots) != 1 or len(active_verities) != 1:
        print("expected exactly one active root/verity pair", file=sys.stderr)
        return 1
    empty_roots = [p for p in root_partitions if p.get("name") == "_empty"]
    empty_verities = [p for p in verity_partitions if p.get("name") == "_empty"]
    if len(empty_roots) != 1 or len(empty_verities) != 1:
        print("expected exactly one empty root slot and one empty verity slot", file=sys.stderr)
        return 1

    root = active_roots[0]
    verity = active_verities[0]
    roothash_file = image.with_name("veyra-next.roothash")
    if not roothash_file.is_file():
        print(f"missing mkosi root hash artifact: {roothash_file}", file=sys.stderr)
        return 1

    encoded_roothash = (root["uuid"].replace("-", "") + verity["uuid"].replace("-", "")).lower()
    roothash = roothash_file.read_text(encoding="ascii").strip().lower()
    if encoded_roothash != roothash:
        print("active root/verity partition UUIDs do not encode the generated dm-verity root hash", file=sys.stderr)
        return 1

    print(f"Veyra A/B image layout and Fedora 44 root/UKI/RPM identity are valid in {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
