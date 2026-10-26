#!/usr/bin/env python3
"""Fail closed on unsafe symlinks in the mkosi image overlay.

Absolute symlinks inside an image may be intentional (systemd units, etc.);
relative symlinks must not escape the overlay root. This source audit runs
before invoking mkosi and does not modify the host or image tree.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def audit(root: Path) -> list[str]:
    if not root.is_dir() or root.is_symlink():
        return [f"invalid overlay root: {root}"]
    problems: list[str] = []
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        link = path.readlink()
        if link.is_absolute():
            # Image-absolute paths refer to the guest filesystem, not host.
            continue
        # Lexically normalize without resolving links on the host. A relative
        # link may refer to a guest path absent from the source overlay.
        depth = len(path.relative_to(root).parts) - 1
        for part in link.parts:
            if part == "..":
                depth -= 1
            elif part not in (".", ""):
                depth += 1
            if depth < 0:
                problems.append(f"relative symlink escapes overlay: {path.relative_to(root)} -> {link}")
                break
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path, nargs="?", default=Path("mkosi.extra"))
    args = parser.parse_args()
    problems = audit(args.overlay)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("Image overlay symlink audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
