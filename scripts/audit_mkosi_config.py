#!/usr/bin/env python3
"""Offline audit of high-risk mkosi.conf settings.

Repeated Packages= entries are intentional. Scalar build/boot keys must have
one unambiguous value so source checks cannot accidentally read a stale one.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "mkosi.conf"
SCALARS = {
    "Distribution": {"Distribution", "Release", "Architecture", "RepositoryKeyFetch"},
    "Output": {"Format", "Output", "OutputDirectory", "ImageId", "ImageVersion"},
    "Content": {"Bootable", "Bootloader", "UnifiedKernelImages", "KernelCommandLine"},
    "Build": {"ToolsTree"},
    "Runtime": {"Firmware"},
}


def audit(content: str) -> list[str]:
    errors: list[str] = []
    section: str | None = None
    seen: dict[tuple[str, str], int] = {}
    sections: set[str] = set()
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"\[([^\]]+)\]", line)
        if match:
            section = match.group(1)
            if section in sections:
                errors.append(f"line {number}: repeated [{section}] section")
            sections.add(section)
            continue
        if section is None:
            errors.append(f"line {number}: entry outside a section")
            continue
        if "=" not in line:
            errors.append(f"line {number}: malformed assignment")
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or not value:
            errors.append(f"line {number}: empty key or value")
            continue
        if key in SCALARS.get(section, set()):
            identity = (section, key)
            if identity in seen:
                errors.append(f"line {number}: duplicate [{section}] {key} (first at {seen[identity]})")
            else:
                seen[identity] = number
    for section, keys in SCALARS.items():
        for key in keys:
            if (section, key) not in seen:
                errors.append(f"missing [{section}] {key}")
    return errors


def main() -> int:
    try:
        content = CONFIG.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"mkosi config unavailable: {exc}", file=sys.stderr)
        return 1
    errors = audit(content)
    for error in errors:
        print(f"mkosi config audit: {error}", file=sys.stderr)
    if errors:
        return 1
    print("mkosi scalar configuration audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
