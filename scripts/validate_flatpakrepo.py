#!/usr/bin/env python3
"""Validate the vendored Flathub remote before image composition.

This checks syntax and key encoding, not the authenticity of the upstream key.
"""
from __future__ import annotations

import base64
import binascii
import configparser
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REMOTE = ROOT / "mkosi.extra/usr/share/veyra/flathub.flatpakrepo"


def validate(text: str) -> list[str]:
    errors: list[str] = []
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        return [f"invalid flatpakrepo syntax: {exc}"]
    if parser.sections() != ["Flatpak Repo"]:
        errors.append("expected exactly one [Flatpak Repo] section")
        return errors
    remote = parser["Flatpak Repo"]
    if remote.get("Url") != "https://dl.flathub.org/repo/":
        errors.append("Flathub URL must match the approved HTTPS endpoint")
    key = remote.get("GPGKey", "")
    if not key:
        errors.append("GPGKey must be present")
    else:
        try:
            decoded = base64.b64decode(key, validate=True)
        except (binascii.Error, ValueError):
            errors.append("GPGKey must be valid base64")
        else:
            if not decoded.startswith(b"\x99") and not decoded.startswith(b"\xc6"):
                errors.append("GPGKey does not begin with an OpenPGP public-key packet")
            if len(decoded) < 256:
                errors.append("GPGKey is unexpectedly short")
    return errors


def main() -> int:
    try:
        content = REMOTE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Flathub remote unavailable: {exc}", file=sys.stderr)
        return 1
    errors = validate(content)
    for error in errors:
        print(f"Flathub remote validation failed: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Vendored Flathub remote syntax and key encoding verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
