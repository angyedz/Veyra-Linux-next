#!/usr/bin/env python3
"""Non-destructive BIOS/UEFI QEMU screenshots and serial diagnostics of a Live ISO.

This is an observational smoke test, NOT proof of a successful graphical boot.
No disk is connected; the ISO is read-only; firmware variables are disposable.
"""
from __future__ import annotations

import argparse
import json
import pathlib
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
    ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE_4M.fd", "/usr/share/edk2/ovmf/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/qemu/OVMF_CODE.fd", "/usr/share/qemu/OVMF_VARS.fd"),
)


class SmokeError(RuntimeError):
    """The diagnostic could not complete, not necessarily a guest boot failure."""


def parse_capture_times(text: str) -> list[int]:
    try:
        times = [int(part) for part in text.split(",")]
    except ValueError as exc:
        raise ValueError("capture times must be comma-separated integers") from exc
    if not 1 <= len(times) <= 3 or times != sorted(set(times)):
        raise ValueError("provide 1–3 distinct, increasing capture times")
    if not (10 <= times[0] and times[-1] <= 300):
        raise ValueError("capture times must be between 10 and 300 seconds")
    return times


def find_ovmf() -> tuple[pathlib.Path, pathlib.Path]:
    for code, variables in OVMF_PAIRS:
        if pathlib.Path(code).is_file() and pathlib.Path(variables).is_file():
            return pathlib.Path(code), pathlib.Path(variables)
    raise SmokeError("Missing OVMF_CODE and OVMF_VARS firmware; install edk2-ovmf/ovmf")


def qemu_args(
    iso: pathlib.Path, mode: str, monitor: pathlib.Path, serial: pathlib.Path,
    memory: int, code: pathlib.Path | None = None,
    variables: pathlib.Path | None = None,
) -> list[str]:
    if mode not in ("bios", "uefi"):
        raise ValueError("mode must be bios or uefi")
    args = [
        "qemu-system-x86_64", "-machine", "q35,accel=tcg",
        "-cpu", "max", "-smp", "2", "-m", str(memory),
        "-vga", "none", "-device", "virtio-vga", "-display", "none", "-nic", "none",
        "-boot", "order=d,menu=off", "-no-reboot",
        "-monitor", f"unix:{monitor},server=on,wait=off",
        "-serial", f"file:{serial}",
        "-drive", f"file={iso},if=ide,media=cdrom,format=raw,readonly=on",
    ]
    if mode == "uefi":
        if code is None or variables is None:
            raise ValueError("UEFI requires OVMF code and disposable variables")
        args.extend((
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
        ))
    return args


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", zlib.crc32(chunk_type + data)))


def ppm_to_png(source: pathlib.Path, destination: pathlib.Path) -> tuple[int, int]:
    """QEMU screendump's binary P6 PPM -> PNG, using only the stdlib."""
    data = source.read_bytes()
    # The last newline is consumed exactly once: a white-space first pixel
    # is not accidentally treated as an additional header separator.
    match = re.match(rb"\AP6\r?\n([0-9]+) ([0-9]+)\r?\n255\r?\n", data)
    if match is None:
        raise SmokeError("QEMU screendump is not a supported P6 8-bit PPM")
    width, height = int(match.group(1)), int(match.group(2))
    if not (64 <= width <= 8192 and 64 <= height <= 8192):
        raise SmokeError("unreasonable screenshot dimensions")
    pixels = data[match.end():]
    if len(pixels) != 3 * width * height:
        raise SmokeError("truncated or malformed PPM screenshot")
    stride = width * 3
    rows = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride]
                    for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    destination.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(rows, 6))
        + png_chunk(b"IEND", b"")
    )
    return width, height


def ppm_has_visible_pixels(source: pathlib.Path) -> bool:
    """Distinguish a totally black captured scanout from a nonblack frame."""
    data = source.read_bytes()
    match = re.match(rb"\AP6\r?\n([0-9]+) ([0-9]+)\r?\n255\r?\n", data)
    if match is None:
        raise SmokeError("invalid QEMU screenshot when checking black frame")
    return any(data[match.end():])


def screendump(monitor: pathlib.Path, ppm: pathlib.Path, deadline: float) -> None:
    while not monitor.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not monitor.exists():
        raise SmokeError("QEMU HMP monitor socket never appeared")
    # Only write to a private temporary path; HMP parses paths as arguments.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(4)
        conn.connect(str(monitor))
        conn.sendall(f"screendump {ppm}\n".encode("utf-8"))
    last_size, stable = -1, 0
    while time.monotonic() < deadline:
        if ppm.is_file():
            size = ppm.stat().st_size
            if size >= 64 and size == last_size:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            last_size = size
        time.sleep(0.2)
    raise SmokeError(f"QEMU screenshot was not completed: {ppm.name}")


def serial_signals(path: pathlib.Path) -> dict[str, bool]:
    data = path.read_bytes() if path.is_file() else b""
    return {
        "kernel_seen": bool(re.search(rb"Linux version|Command line:|Kernel command line:", data, re.I)),
        "init_seen": bool(re.search(rb"systemd\[1\]|dracut|Reached target", data, re.I)),
        "live_desktop_confirmed": False,  # Only visual/human inspection can confirm this.
        "plasma_session_crashed": bool(re.search(rb"Session crashed \(exit code [0-9]+\)", data, re.I)),
        "pam_systemd_missing": b"PAM unable to dlopen(" in data and b"pam_systemd.so" in data,
        "plasma_session_invoked": b"startplasma-wayland" in data,
    }


def capture_one(
    iso: pathlib.Path, output: pathlib.Path, mode: str,
    times: list[int], memory: int, ovmf: tuple[pathlib.Path, pathlib.Path] | None,
) -> dict:
    target = output / mode
    target.mkdir(mode=0o700)
    record: dict = {
        "mode": mode,
        "screenshots": [],
        "errors": [],
        "automatically_verified_graphical_boot": False,
    }
    with tempfile.TemporaryDirectory(prefix=f"veyra-{mode}-") as scratch:
        temp = pathlib.Path(scratch)
        monitor = temp / "monitor.sock"
        code, variables = None, None
        if mode == "uefi":
            if ovmf is None:
                raise SmokeError("OVMF firmware unavailable")
            code, template = ovmf
            variables = temp / "OVMF_VARS.fd"
            shutil.copyfile(template, variables)
        stdout = target / "qemu.stdout"
        stderr = target / "qemu.stderr"
        serial = target / "serial.log"
        args = qemu_args(iso, mode, monitor, serial, memory, code, variables)
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(args, stdout=out, stderr=err)
            start = time.monotonic()
            try:
                for second in times:
                    while time.monotonic() - start < second:
                        if process.poll() is not None:
                            raise SmokeError(f"QEMU exited before {second}s (exit {process.returncode})")
                        time.sleep(0.2)
                    ppm = temp / f"screen-{second:03d}.ppm"
                    png = target / f"screen-{second:03d}.png"
                    screendump(monitor, ppm, time.monotonic() + 20)
                    size = ppm_to_png(ppm, png)
                    record["screenshots"].append({
                        "seconds_after_launch": second,
                        "file": png.name,
                        "dimensions": list(size),
                        "non_black_pixels_detected": ppm_has_visible_pixels(ppm),
                    })
            except (OSError, SmokeError) as exc:
                record["errors"].append(str(exc))
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)
                record["qemu_exit_code_after_cleanup"] = process.returncode
    record["serial_signals"] = serial_signals(target / "serial.log")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("build/iso-qemu"))
    parser.add_argument("--modes", choices=("bios", "uefi", "both"), default="both")
    parser.add_argument("--at", default="20,60", help="capture seconds, 1–3 numbers (10–300)")
    parser.add_argument("--memory", type=int, default=3072, help="RAM in MiB (1024–6144)")
    args = parser.parse_args(argv)
    try:
        times = parse_capture_times(args.at)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1024 <= args.memory <= 6144:
        parser.error("--memory must be 1024–6144 MiB")
    if not args.iso.is_file() or args.iso.stat().st_size == 0:
        parser.error("--iso must point to one nonempty ISO file")
    iso = args.iso.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("output already exists (will not overwrite or follow symlinks)")
    if not shutil.which("qemu-system-x86_64"):
        parser.error("qemu-system-x86_64 missing")
    modes = ("bios", "uefi") if args.modes == "both" else (args.modes,)
    ovmf = None
    if "uefi" in modes:
        try:
            ovmf = find_ovmf()
        except SmokeError as exc:
            parser.error(str(exc))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {
        "iso": str(iso),
        "cdrom_readonly": True,
        "network_disabled": True,
        "efi_variables_temporary": True,
        "diagnostic_only": True,
        "modes": [],
    }
    failed = False
    try:
        for mode in modes:
            print(f"Capturing {mode.upper()} VM boot stages...", flush=True)
            result = capture_one(iso, output, mode, times, args.memory, ovmf)
            failed = failed or bool(result["errors"])
            report["modes"].append(result)
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Diagnostics in {output}. These screenshots are NOT a boot success assertion.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
