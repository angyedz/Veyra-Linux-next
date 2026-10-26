#!/usr/bin/env python3
"""Copy the separately built rescue UKI into the main image's ESP tree."""

from __future__ import annotations

import argparse
import pathlib
import shutil
import stat


class RecoveryImageError(RuntimeError):
    pass


def install_recovery_uki(root: pathlib.Path, output_dir: pathlib.Path) -> pathlib.Path:
    matches = sorted(output_dir.glob("veyra-recovery*.efi"))
    if len(matches) != 1:
        raise RecoveryImageError(
            f"expected exactly one recovery UKI in {output_dir}, found {len(matches)}"
        )
    artifact = matches[0]
    if not stat.S_ISREG(artifact.lstat().st_mode):
        raise RecoveryImageError("the recovery UKI must be a regular file")

    destination = root / "efi/EFI/Linux/veyra-recovery.efi"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact, destination)
    destination.chmod(0o644)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        destination = install_recovery_uki(args.root, args.output_dir)
    except (OSError, RecoveryImageError) as exc:
        parser.error(str(exc))
    print(f"Installed independent Recovery UKI at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
