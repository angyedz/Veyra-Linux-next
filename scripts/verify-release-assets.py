#!/usr/bin/env python3
"""Fail-closed GitHub Releases asset gate for one signed Veyra A/B version."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re

# Below both GitHub's advertised 2 GiB bound and a decimal 2 GB budget.
MAX_ASSET_BYTES = 2_000_000_000
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[.-][a-z0-9]+)*")


def validate(directory: Path, version: str) -> list[str]:
    if not VERSION.fullmatch(version):
        raise ValueError("unsafe or invalid update release version")
    expected = [
        f"veyra-next_{version}.efi",
        f"veyra-next_{version}.vmlinuz",
        f"veyra-next_{version}.initrd",
    ]
    roots = sorted(directory.glob(f"veyra-next_{version}_*.root.raw.zst"))
    verities = sorted(directory.glob(f"veyra-next_{version}_*.root-verity.raw.zst"))
    if len(roots) != 1 or len(verities) != 1:
        raise ValueError("expected exactly one root and one matching verity image")
    expected.extend((roots[0].name, verities[0].name))
    digest = re.compile(rf"veyra-next_{re.escape(version)}_([0-9a-f]{{64}})\.root(?:-verity)?\.raw\.zst")
    matches = [digest.fullmatch(p.name) for p in (roots[0], verities[0])]
    if any(m is None for m in matches) or matches[0].group(1) != matches[1].group(1):
        raise ValueError("root and verity files must use one identical dm-verity root hash")
    expected.extend(("SHA256SUMS", "SHA256SUMS.gpg"))
    for name in expected:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"release asset missing or unsafe: {name}")
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise ValueError(f"release asset exceeds 2 GB: {name}")
    lines = (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    checksums: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+)", line)
        if match is None or match.group(2) in checksums:
            raise ValueError("release checksum manifest is malformed or contains duplicate files")
        checksums[match.group(2)] = match.group(1)
    if set(checksums) != set(expected[:-2]):
        raise ValueError("release manifest does not list precisely the five OTA assets")
    for name, wanted in checksums.items():
        h = hashlib.sha256()
        with (directory / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != wanted:
            raise ValueError(f"checksum mismatch: {name}")
    # Detached GPG signature verification is performed independently by
    # gpg --verify before publication, using the dedicated update key.
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        paths = validate(args.directory, args.version)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, f"Veyra update release rejected: {exc}\n")
    print(f"Checked {len(paths)} GitHub Release assets; each is at most {MAX_ASSET_BYTES} bytes.")
