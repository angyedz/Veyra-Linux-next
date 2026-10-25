#!/usr/bin/env python3
"""Prepare mkosi's verity partitions and UKI for a Veyra sysupdate release."""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def image_version() -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    version = image_version()
    versioned_uki = output_dir / f"veyra-next_{version}.efi"
    for stale in output_dir.iterdir():
        if not stale.is_file():
            continue
        if stale.name.endswith((".root.raw.zst", ".root-verity.raw.zst")):
            stale.unlink()
        elif stale.name.startswith("veyra-next_") and stale.name.endswith(".efi") and stale != versioned_uki:
            stale.unlink()

    uki = output_dir / "veyra-next.efi"
    if not uki.is_file() or uki.stat().st_size == 0:
        print(f"missing mkosi UKI output: {uki}", file=sys.stderr)
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

    shutil.copy2(uki, versioned_uki)
    for artifact in normalized.values():
        subprocess.run(["zstd", "--rm", "-T0", "-9", str(artifact)], check=True)

    print(f"Prepared Veyra {version} root, verity, and UKI artifacts in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
