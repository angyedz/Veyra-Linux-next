#!/usr/bin/env python3
"""Apply the same safe-payload policy to package-repository build inputs."""

from __future__ import annotations

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPKG = ROOT / "mkosi.extra/usr/bin/vpkg"
SPEC = importlib.util.spec_from_loader(
    "veyra_vpkg_policy", SourceFileLoader("veyra_vpkg_policy", str(VPKG))
)
assert SPEC and SPEC.loader
vpkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vpkg)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rpm_directory", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    arguments = parser.parse_args()
    vpkg.inspect_extension_rpms(arguments.rpm_directory, allow_empty=arguments.allow_empty)
    print("RPM extension payload checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
