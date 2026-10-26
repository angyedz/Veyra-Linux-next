#!/usr/bin/env python3
"""Read-only Veyra boot profiler: export boot critical path and slow units.

Run on a booted Veyra installation, never during image build. This script
only invokes systemd-analyze and writes a report; it changes no services.
All commands finish before any report is published.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


COMMANDS = {
    "boot-time.txt": ["systemd-analyze", "time"],
    "critical-chain.txt": ["systemd-analyze", "critical-chain"],
    "unit-blame.txt": ["systemd-analyze", "blame"],
}


def collect(output: Path) -> None:
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError("output must be a real directory")
    # Collect first: a failing systemd-analyze invocation must not leave
    # an incomplete or misleading set of published timing reports.
    reports = {}
    for name, command in COMMANDS.items():
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=45)
        if result.returncode:
            raise RuntimeError(f"{' '.join(command)} failed: {result.stderr.strip()}")
        reports[name] = result.stdout
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("refusing symlink output directory")
    for name in COMMANDS:
        destination = output / name
        if destination.is_symlink():
            raise ValueError(f"refusing symlink report destination: {destination}")
        if destination.exists():
            raise ValueError(f"refusing to overwrite existing report: {destination}")
    # Stage in the same directory and atomically rename each report.
    # No writes to system files or service configuration.
    staged = []
    try:
        for name, data in reports.items():
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=output, prefix=".veyra-boot-",
                                             delete=False) as file:
                staged.append(Path(file.name))
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
        for path, name in zip(staged, reports):
            os.replace(path, output / name)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
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
