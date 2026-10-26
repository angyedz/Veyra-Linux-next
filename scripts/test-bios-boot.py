#!/usr/bin/env python3
"""Boot the built GPT image through SeaBIOS and require graphical.target."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time

BIOS_BOOT_TYPE = "21686148-6449-6e6f-744e-656564454649"
EFI_SYSTEM_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
ROOT_TYPE = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
BOOT_MARKER = b"VEYRA_BIOS_BOOT_CI_PASS"


def check_layout(image: pathlib.Path) -> None:
    result = subprocess.run(
        ["sfdisk", "--json", str(image)], check=True, capture_output=True, text=True
    )
    partitions = json.loads(result.stdout).get("partitiontable", {}).get("partitions", [])
    types = [str(partition.get("type", "")).lower() for partition in partitions]
    for partition_type, description in (
        (BIOS_BOOT_TYPE, "GRUB BIOS Boot Partition"),
        (EFI_SYSTEM_TYPE, "EFI System Partition"),
        (ROOT_TYPE, "Veyra root partition"),
    ):
        if partition_type not in types:
            raise ValueError(f"image has no {description}")


def log_tail(path: pathlib.Path, lines: int = 80) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(log unavailable)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--report-dir", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    if not args.image.is_file() or args.image.stat().st_size == 0:
        parser.error(f"image is missing or empty: {args.image}")
    if not 10 <= args.timeout <= 1800:
        parser.error("--timeout must be between 10 and 1800 seconds")
    if not shutil.which("qemu-system-x86_64"):
        parser.error("qemu-system-x86_64 is required")

    try:
        check_layout(args.image)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as error:
        print(f"Legacy BIOS image-layout check failed: {error}", file=sys.stderr)
        return 1

    args.report_dir.mkdir(parents=True, exist_ok=True)
    serial_log = args.report_dir / "serial.log"
    qemu_stdout = args.report_dir / "qemu.stdout"
    qemu_stderr = args.report_dir / "qemu.stderr"
    command = [
        "qemu-system-x86_64",
        "-machine",
        "pc,accel=tcg",
        "-cpu",
        "max",
        "-smp",
        "2",
        "-m",
        "2048",
        "-display",
        "none",
        "-monitor",
        "none",
        "-no-reboot",
        "-boot",
        "order=c,menu=off",
        "-drive",
        f"if=ide,index=0,format=raw,snapshot=on,file={args.image.resolve()}",
        "-serial",
        f"file:{serial_log.resolve()}",
    ]

    start = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    passed = False
    timed_out = False
    try:
        with qemu_stdout.open("wb") as stdout, qemu_stderr.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            while time.monotonic() - start < args.timeout:
                if serial_log.exists() and BOOT_MARKER in serial_log.read_bytes():
                    passed = True
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.5)
            timed_out = not passed and process.poll() is None
    except OSError as error:
        print(f"Could not start SeaBIOS QEMU: {error}", file=sys.stderr)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    if passed:
        elapsed = int(time.monotonic() - start)
        print(f"Legacy BIOS boot reached graphical.target in {elapsed}s; logs: {args.report_dir}")
        return 0

    state = "timed out" if timed_out else "QEMU exited before the marker"
    print(f"Legacy BIOS boot {state}; expected {BOOT_MARKER.decode()}.", file=sys.stderr)
    print("--- serial output ---", file=sys.stderr)
    print(log_tail(serial_log), file=sys.stderr)
    print("--- QEMU stderr ---", file=sys.stderr)
    print(log_tail(qemu_stderr), file=sys.stderr)
    print(f"Full diagnostics: {args.report_dir}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
