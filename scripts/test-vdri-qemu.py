#!/usr/bin/env python3
"""Boot the built Fedora 44 image and verify v-dri against a virtual GPU."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


OVMF_PAIRS = (
    (Path("/usr/share/OVMF/OVMF_CODE_4M.fd"), Path("/usr/share/OVMF/OVMF_VARS_4M.fd")),
    (Path("/usr/share/OVMF/OVMF_CODE.fd"), Path("/usr/share/OVMF/OVMF_VARS.fd")),
    (Path("/usr/share/ovmf/OVMF_CODE.fd"), Path("/usr/share/ovmf/OVMF_VARS.fd")),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"image does not exist: {args.image}")
    firmware = next(((code, variables) for code, variables in OVMF_PAIRS if code.is_file() and variables.is_file()), None)
    if firmware is None:
        print("Could not find a matching OVMF code/variables pair.", file=sys.stderr)
        return 2
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        print("qemu-system-x86_64 is not installed.", file=sys.stderr)
        return 2

    code, variables_template = firmware
    with tempfile.TemporaryDirectory(prefix="veyra-vdri-qemu-") as temporary:
        variables = Path(temporary) / "OVMF_VARS.fd"
        shutil.copyfile(variables_template, variables)
        command = [
            qemu,
            "-machine", "q35,accel=tcg",
            "-m", "4096",
            "-smp", "4",
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={variables}",
            "-drive", f"if=virtio,format=raw,file={args.image}",
            "-snapshot",
            "-device", "virtio-vga",
            "-display", "none",
            "-serial", "stdio",
            "-monitor", "none",
            "-no-reboot",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout, check=False)
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
            print(stdout + stderr, file=sys.stderr)
            print(f"QEMU did not finish within {args.timeout} seconds.", file=sys.stderr)
            return 1

    if result.returncode != 0 or "VDRI_QEMU_PASS" not in output:
        print(output[-24000:], file=sys.stderr)
        print(f"QEMU/v-dri smoke check failed (QEMU exit {result.returncode}).", file=sys.stderr)
        return 1
    print("Fedora 44 QEMU verified module-signature enforcement and the bound virtio_gpu driver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
