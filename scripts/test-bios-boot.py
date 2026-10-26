#!/usr/bin/env python3
"""Boot the built GPT image through SeaBIOS and require graphical.target."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
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


def add_serial_diagnostics(grub_config: str) -> str:
    """Add CI-only serial boot logs to the stable and trial GRUB kernel entries."""
    diagnostic_arguments = (
        "console=tty0 console=ttyS0,115200n8 "
        "systemd.show_status=1 systemd.log_target=console "
    )
    updated_lines: list[str] = []
    entries = 0
    for line in grub_config.splitlines(keepends=True):
        if line.lstrip().startswith("if linux "):
            if "console=ttyS0,115200n8" in line or "systemd.log_target=console" in line:
                raise ValueError("GRUB configuration already contains serial diagnostics")
            marker = "systemd.unit=graphical.target;"
            if marker not in line:
                raise ValueError("GRUB kernel entry has no expected graphical.target command line")
            line = line.replace(marker, diagnostic_arguments + marker, 1)
            entries += 1
        updated_lines.append(line)

    if entries != 2:
        raise ValueError(f"expected 2 Veyra GRUB kernel entries, found {entries}")
    return "".join(updated_lines)


@contextmanager
def serial_console_diagnostics(image: pathlib.Path) -> Iterator[None]:
    """Temporarily add serial logging to the ESP GRUB config, then restore it."""
    if not shutil.which("mcopy"):
        raise RuntimeError("mcopy is required to prepare BIOS serial diagnostics")

    table = json.loads(
        subprocess.run(
            ["sfdisk", "--json", str(image)], check=True, capture_output=True, text=True
        ).stdout
    )["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    esps = [
        partition
        for partition in table.get("partitions", [])
        if str(partition.get("type", "")).lower() == EFI_SYSTEM_TYPE
    ]
    if len(esps) != 1:
        raise ValueError(f"expected exactly one EFI System Partition, found {len(esps)}")
    image_ref = f"{image.resolve()}@@{int(esps[0]['start']) * sector_size}"

    with tempfile.TemporaryDirectory(prefix="veyra-bios-serial-") as temporary:
        temporary_path = pathlib.Path(temporary)
        original = temporary_path / "grub.cfg.original"
        diagnostic = temporary_path / "grub.cfg.diagnostic"
        subprocess.run(
            ["mcopy", "-i", image_ref, "::/grub2/grub.cfg", str(original)], check=True
        )
        original_content = original.read_bytes()
        diagnostic.write_text(
            add_serial_diagnostics(original_content.decode("ascii")), encoding="ascii"
        )
        try:
            subprocess.run(
                ["mcopy", "-o", "-i", image_ref, str(diagnostic), "::/grub2/grub.cfg"],
                check=True,
            )
            yield
        finally:
            restore = temporary_path / "grub.cfg.restore"
            restore.write_bytes(original_content)
            subprocess.run(
                ["mcopy", "-o", "-i", image_ref, str(restore), "::/grub2/grub.cfg"],
                check=True,
            )


def log_tail(path: pathlib.Path, lines: int = 80) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(log unavailable)"


def capture_screen(monitor_socket: pathlib.Path, destination: pathlib.Path) -> bool:
    """Save the text VGA screen if the firmware or GRUB never reaches serial output."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if monitor_socket.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as monitor:
                    monitor.settimeout(2)
                    monitor.connect(str(monitor_socket))
                    try:
                        monitor.recv(4096)
                    except TimeoutError:
                        pass
                    monitor.sendall(f"screendump {destination}\n".encode("ascii"))
                    time.sleep(0.5)
                    return destination.is_file() and destination.stat().st_size > 0
            except (OSError, TimeoutError):
                return False
        time.sleep(0.1)
    return False


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
    monitor_socket = pathlib.Path(tempfile.gettempdir()) / f"veyra-bios-qemu-{os.getpid()}.sock"
    screen_dump = args.report_dir / "screen.ppm"
    monitor_socket.unlink(missing_ok=True)

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
        "-vga",
        "std",
        "-display",
        "none",
        "-monitor",
        f"unix:{monitor_socket},server=on,wait=off",
        "-no-reboot",
        "-boot",
        "order=c,menu=off",
        "-drive",
        f"if=ide,index=0,format=raw,snapshot=on,file={args.image.resolve()}",
        "-serial",
        f"file:{serial_log.resolve()}",
    ]

    try:
        with serial_console_diagnostics(args.image):
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
                    if not passed:
                        capture_screen(monitor_socket, screen_dump)
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        print(f"Could not prepare the BIOS serial diagnostic: {error}", file=sys.stderr)
        return 1
    finally:
        monitor_socket.unlink(missing_ok=True)

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
    if screen_dump.is_file() and screen_dump.stat().st_size > 0:
        print(f"VGA screenshot: {screen_dump}", file=sys.stderr)
    print(f"Full diagnostics: {args.report_dir}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
