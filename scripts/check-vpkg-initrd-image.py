#!/usr/bin/env python3
"""Assert that the built initrd carries executable vpkg generation code."""

from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path


def cpio_entries(data: bytes) -> dict[str, tuple[int, bytes]]:
    entries: dict[str, tuple[int, bytes]] = {}
    offset = 0
    while offset < len(data):
        header = data[offset : offset + 110]
        if len(header) != 110 or header[:6] not in {b"070701", b"070702"}:
            raise ValueError(f"invalid newc CPIO header at byte {offset}")
        fields = [int(header[index : index + 8], 16) for index in range(6, 110, 8)]
        mode, file_size, name_size = fields[1], fields[6], fields[11]
        offset += 110
        if not name_size or name_size > 65536 or file_size > len(data):
            raise ValueError("invalid newc CPIO entry dimensions")
        raw_name = data[offset : offset + name_size]
        if len(raw_name) != name_size or raw_name[-1:] != b"\0":
            raise ValueError("invalid newc CPIO entry name")
        name = raw_name[:-1].decode("utf-8", errors="strict").lstrip("./")
        offset = (offset + name_size + 3) & ~3
        payload = data[offset : offset + file_size]
        if len(payload) != file_size:
            raise ValueError(f"truncated newc CPIO entry: {name}")
        offset = (offset + file_size + 3) & ~3
        if name == "TRAILER!!!":
            break
        entries[name] = (mode, payload)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("initrd", type=Path)
    args = parser.parse_args()
    try:
        archive = subprocess.run(
            ["zstd", "-dc", str(args.initrd)], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        entries = cpio_entries(archive)
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        print(f"could not inspect initrd {args.initrd}: {error}", file=sys.stderr)
        return 1
    helper = "usr/libexec/veyra-vpkg-initrd.py"
    unit = "usr/lib/systemd/system/veyra-vpkg-generations.service"
    enablement = "usr/lib/systemd/system/initrd-root-fs.target.requires/veyra-vpkg-generations.service"
    required = {helper, unit, enablement}
    missing = sorted(required - set(entries))
    if missing:
        print("initrd is missing vpkg files: " + ", ".join(missing), file=sys.stderr)
        return 1
    mode, helper_payload = entries[helper]
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o755:
        print("initrd generation helper must be a regular executable with mode 0755", file=sys.stderr)
        return 1
    mode, _ = entries[unit]
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o644:
        print("initrd generation service must be a regular file with mode 0644", file=sys.stderr)
        return 1
    mode, link_target = entries[enablement]
    if not stat.S_ISLNK(mode) or link_target != b"../veyra-vpkg-generations.service":
        print("initrd root-fs target must require the vpkg generation service", file=sys.stderr)
        return 1
    source_helper = Path("mkosi.images/initrd/mkosi.extra") / helper
    if not source_helper.is_file() or helper_payload != source_helper.read_bytes():
        print("built initrd generation helper does not match its source", file=sys.stderr)
        return 1
    print(f"vpkg initrd helper and service are present with expected modes in {args.initrd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
