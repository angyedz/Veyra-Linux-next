#!/usr/bin/env python3
"""Capture guest firmware/first-boot screens from a *disposable* QEMU snapshot.

This does NOT claim graphical Plasma login. First boot may show Plasma Setup,
a bootloader prompt, a kernel error, or a blank screen. Review PNGs manually.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

OVMF_PAIRS = (
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE_4M.fd", "/usr/share/edk2/ovmf/OVMF_VARS_4M.fd"),
)


def firmware_pair() -> tuple[Path, Path]:
    for code, variables in OVMF_PAIRS:
        if Path(code).is_file() and Path(variables).is_file():
            return Path(code), Path(variables)
    raise RuntimeError("OVMF firmware not found; install ovmf/edk2-ovmf")


def parse_times(spec: str) -> list[int]:
    try:
        times = [int(item) for item in spec.split(",")]
    except ValueError as exc:
        raise ValueError("--at requires comma-separated integer seconds") from exc
    if not (1 <= len(times) <= 3 and 10 <= times[0] and times[-1] <= 300):
        raise ValueError("set 1-3 captures between 10 and 300 seconds")
    if times != sorted(set(times)):
        raise ValueError("capture times must be strictly increasing and unique")
    return times


def qemu_command(image: Path, code: Path, variables: Path, monitor: Path,
                 serial: Path, ram: int) -> list[str]:
    # Existing raw image never receives persistent guest writes.
    # EFI variable writes affect ONLY our copied OVMF_VARS.fd.
    return [
        "qemu-system-x86_64", "-machine", "q35,accel=tcg",
        "-cpu", "max", "-smp", "2", "-m", str(ram),
        "-display", "none", "-vga", "std", "-nic", "none",
        "-qmp", f"unix:{monitor},server=on,wait=off",
        "-serial", f"file:{serial}", "-no-reboot",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
        "-drive", f"if=virtio,format=raw,snapshot=on,file={image}",
    ]


def png_chunk(name: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body))


def ppm_to_png(source: Path, destination: Path) -> tuple[int, int]:
    """Convert QEMU P6 RGB screendump with stdlib only; reject truncated files."""
    raw = source.read_bytes()
    match = re.match(rb"^P6\s+([0-9]+)\s+([0-9]+)\s+255\s", raw)
    if match is None:
        raise ValueError("QEMU screendump is not a supported P6/8-bit PPM")
    w, h = (int(match.group(1)), int(match.group(2)))
    if not (64 <= w <= 8192 and 64 <= h <= 8192):
        raise ValueError("unreasonable screenshot dimensions")
    pixels = raw[match.end():]
    if len(pixels) != w * h * 3:
        raise ValueError("truncated or malformed screenshot pixel data")
    row_bytes = w * 3
    scanlines = b"".join(bytes((0,)) + pixels[y * row_bytes:(y + 1) * row_bytes]
                         for y in range(h))
    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (bytes.fromhex("89504e470d0a1a0a")
           + png_chunk(b"IHDR", header)
           + png_chunk(b"IDAT", zlib.compress(scanlines, 6))
           + png_chunk(b"IEND", b""))
    destination.write_bytes(png)
    return w, h


def _qmp_request(sock: socket.socket, source, name: str,
                 arguments: dict | None = None) -> dict:
    """Send one QMP request and wait for its matching acknowledgement."""
    request = {"execute": name, "id": f"veyra-{name}"}
    if arguments is not None:
        request["arguments"] = arguments
    sock.sendall((json.dumps(request) + "\r\n").encode("utf-8"))
    # QMP can emit asynchronous events between replies.
    for _ in range(50):
        line = source.readline()
        if not line:
            raise RuntimeError(f"QMP disconnected during {name}")
        response = json.loads(line)
        if response.get("id") != request["id"]:
            continue
        if "error" in response:
            raise RuntimeError(f"QMP {name} failed: {response['error']}")
        if "return" not in response:
            raise RuntimeError(f"Unexpected QMP {name} response: {response}")
        return response
    raise RuntimeError(f"Too many asynchronous QMP events during {name}")


def screendump(monitor: Path, destination: Path, timeout_seconds: int = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not monitor.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not monitor.exists():
        raise RuntimeError("QEMU QMP monitor socket unavailable")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_seconds)
        sock.connect(str(monitor))
        with sock.makefile("rb") as incoming:
            handshake = json.loads(incoming.readline())
            if "QMP" not in handshake:
                raise RuntimeError(f"Missing QMP greeting: {handshake}")
            _qmp_request(sock, incoming, "qmp_capabilities")
            _qmp_request(sock, incoming, "screendump",
                         {"filename": str(destination)})
    # QMP reply confirms completion, but still validate actual artifact.
    while time.monotonic() < deadline:
        if destination.is_file() and destination.stat().st_size > 64:
            return
        time.sleep(0.25)
    raise RuntimeError(f"QMP acknowledged screenshot without file: {destination.name}")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="mkosi.output/veyra-next.raw")
    parser.add_argument("--output", default="qemu-glacier-visual-report")
    parser.add_argument("--at", default="25,80",
                        help="up to 3 screenshot times in seconds, 10..300 (default: 25,80)")
    parser.add_argument("--memory", type=int, default=4096, help="MiB, 1024..8192")
    args = parser.parse_args(argv)
    try:
        times = parse_times(args.at)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1024 <= args.memory <= 8192:
        parser.error("--memory must be 1024..8192 MiB")
    try:
        image = Path(args.image).resolve(strict=True)
    except FileNotFoundError:
        parser.error("--image not found")
    if not image.is_file() or image.stat().st_size == 0:
        parser.error("--image must be a nonempty disk image")
    dest = Path(args.output).absolute()
    if dest.is_symlink() or dest.exists():
        parser.error("report directory already exists; refusing to overwrite or follow a symlink")
    if not shutil.which("qemu-system-x86_64"):
        parser.error("qemu-system-x86_64 is not installed")
    try:
        code, vars_template = firmware_pair()
    except RuntimeError as exc:
        parser.error(str(exc))
    dest.mkdir(mode=0o700, parents=False, exist_ok=False)
    outcome = {
        "image": str(image),
        "firmware": str(code),
        "snapshot_only": True,
        "kind": "diagnostic (NOT a successful graphical desktop proof)",
        "screens": [],
        "errors": [],
    }
    with tempfile.TemporaryDirectory(prefix="veyra-qemu-") as temp:
        tempdir = Path(temp)
        vars_copy = tempdir / "OVMF_VARS.fd"
        shutil.copyfile(vars_template, vars_copy)
        monitor = tempdir / "qmp.sock"
        command = qemu_command(image, code, vars_copy, monitor,
                               dest / "serial.log", args.memory)
        with (dest / "qemu.stdout").open("wb") as stdout, (dest / "qemu.stderr").open("wb") as stderr:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            start = time.monotonic()
            try:
                for second in times:
                    while time.monotonic() - start < second:
                        if proc.poll() is not None:
                            raise RuntimeError(f"QEMU exited early (code {proc.returncode})")
                        time.sleep(0.25)
                    ppm = dest / f"screen-{second:03d}s.ppm"
                    png = dest / f"screen-{second:03d}s.png"
                    screendump(monitor, ppm)
                    width, height = ppm_to_png(ppm, png)
                    ppm.unlink()
                    outcome["screens"].append({"at_seconds": second, "file": png.name,
                                               "width": width, "height": height})
            except (OSError, RuntimeError, ValueError) as exc:
                outcome["errors"].append(str(exc))
            finally:
                proc.terminate() if proc.poll() is None else None
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=8)
                outcome["qemu_exit_after_termination"] = proc.returncode
    (dest / "metadata.json").write_text(json.dumps(outcome, indent=2) + "\n")
    print(f"Saved {len(outcome['screens'])} screenshots and logs in {dest}")
    if outcome["errors"]:
        print("; ".join(outcome["errors"]), file=sys.stderr)
        return 1
    print("Review screenshots manually: capturing a firmware/setup/login screen is NOT a desktop QA pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
