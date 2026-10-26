#!/usr/bin/env python3
"""Read-only Veyra boot profiler: export boot critical path and slow units.

Run on a booted Veyra installation, never during image build. This script
only invokes systemd-analyze and writes a report; it changes no services.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


COMMANDS = {
    "boot-time.txt": ["systemd-analyze", "time"],
    "critical-chain.txt": ["systemd-analyze", "critical-chain"],
    "unit-blame.txt": ["systemd-analyze", "blame"],
}


def collect(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise ValueError("output is not a directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, command in COMMANDS.items():
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=45)
        if result.returncode:
            raise RuntimeError(f"{' '.join(command)} failed: {result.stderr.strip()}")
        destination = output / name
        if destination.is_symlink():
            raise ValueError(f"refusing symlink report destination: {destination}")
        destination.write_text(result.stdout, encoding="utf-8")
    print(f"Boot diagnostics saved in {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("veyra-boot-report"))
    args = parser.parse_args()
    try:
        collect(args.output)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Boot profiling failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
