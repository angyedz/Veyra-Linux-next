#!/usr/bin/env python3
"""Verify Veyra Plymouth is installed in a built image (run inside mkosi shell).

Source tests alone cannot prove the theme survived image assembly.
"""
from __future__ import annotations

import configparser
from pathlib import Path
import sys


THEME = Path("/usr/share/plymouth/themes/veyra")


def check(theme: Path = THEME, default_theme: Path = Path("/usr/share/plymouth/themes/default.plymouth")) -> list[str]:
    issues: list[str] = []
    descriptor = theme / "veyra.plymouth"
    script = theme / "veyra.script"
    if not descriptor.is_file() or not script.is_file():
        return ["missing installed Veyra Plymouth theme files"]
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(descriptor, encoding="utf-8")
        if config.get("Plymouth Theme", "ModuleName") != "script":
            issues.append("Plymouth theme is not script-based")
        if config.get("script", "ScriptFile") != str(script):
            issues.append("Plymouth theme script path is incorrect")
    except (configparser.Error, KeyError, ValueError) as exc:
        issues.append(f"invalid Plymouth descriptor: {exc}")
    if not default_theme.is_symlink():
        issues.append("default Plymouth theme symlink missing")
    else:
        # A guest-absolute link is valid; resolve it inside the running guest.
        if default_theme.resolve() != descriptor.resolve():
            issues.append("default Plymouth theme does not point to Veyra")
    return issues


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"Plymouth image check: {error}", file=sys.stderr)
        return 1
    print("Veyra Plymouth theme installed and selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
