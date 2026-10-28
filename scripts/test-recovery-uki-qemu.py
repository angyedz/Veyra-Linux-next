#!/usr/bin/env python3
"""Boot a Recovery UKI by itself in disposable UEFI QEMU and observe its menu.

No installed system, host block device, or persistent EFI variables are used.
This is independent Recovery boot evidence, NOT proof of recovery/rollback.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
DISK_BYTES = 512 * 1024 * 1024
ESP_START = 2048             # 1 MiB in 512-byte sectors
ESP_SECTORS = 900000         # approximately 439 MiB, enough for the UKI
OVMF_PAIRS = (
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE_4M.fd", "/usr/share/edk2/ovmf/OVMF_VARS_4M.fd"),
)
BOOT_TEXT = "Veyra Recovery"
MENU_TEXT = "Select an action:"


class RecoveryQemuError(RuntimeError):
    pass


def check_uki(path: pathlib.Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RecoveryQemuError("Recovery UKI must be a real regular file")
    if path.stat().st_size >= ESP_SECTORS * 512 - 32 * 1024 * 1024:
        raise RecoveryQemuError("Recovery UKI is too large for the disposable FAT ESP")
    # Reuse the installer preflight identity/bounds checks; never reduce
    # the Recovery identity gate to a nonempty .efi existence test.
    installer_path = pathlib.Path(__file__).resolve().parent / "veyra-install.py"
    spec = importlib.util.spec_from_file_location("veyra_installer_recovery_check", installer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.validate_recovery_uki_metadata(path)
    except module.InstallError as error:
        raise RecoveryQemuError(str(error)) from error


def run(command: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        command, input=input_text, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise RecoveryQemuError(
            f"command {command[0]} exited {result.returncode}: {result.stderr.strip()[:1800]}"
        )
    return result.stdout


def make_esp(disk: pathlib.Path, uki: pathlib.Path) -> None:
    # These are the only disk-writing commands. 'disk' is constructed inside
    # a fresh TemporaryDirectory, never from a user-supplied block path.
    with disk.open("xb") as image:
        image.truncate(DISK_BYTES)
    layout = f"label: gpt\nunit: sectors\nstart={ESP_START}, size={ESP_SECTORS}, type={ESP_GUID}, name=VEYRA_RECOVERY_TEST\n"
    run(["sfdisk", "--wipe", "always", str(disk)], input_text=layout)
    partition_table = json.loads(run(["sfdisk", "--json", str(disk)]))["partitiontable"]
    partitions = partition_table["partitions"]
    if (len(partitions) != 1
            or int(partitions[0]["start"]) != ESP_START
            or int(partitions[0]["size"]) != ESP_SECTORS
            or partitions[0]["type"].lower() != ESP_GUID):
        raise RecoveryQemuError("temporary Recovery test disk GPT does not match expected EFI layout")
    # mkfs.fat --offset and explicit size prevent formatting the backup GPT.
    run(["mkfs.vfat", "-F", "32", "--offset", str(ESP_START),
         str(disk), str(ESP_SECTORS // 2)])
    mtools = f"{disk}@@{ESP_START * 512}"
    run(["mmd", "-i", mtools, "::/EFI", "::/EFI/BOOT"])
    run(["mcopy", "-i", mtools, str(uki), "::/EFI/BOOT/BOOTX64.EFI"])


def get_ovmf() -> tuple[pathlib.Path, pathlib.Path]:
    for c, v in OVMF_PAIRS:
        code, variables = pathlib.Path(c), pathlib.Path(v)
        if code.is_file() and variables.is_file():
            return code, variables
    raise RecoveryQemuError("matching OVMF_CODE/OVMF_VARS firmware pair is unavailable")


def qemu_command(
    disk: pathlib.Path, serial: pathlib.Path, code: pathlib.Path, variables: pathlib.Path,
) -> list[str]:
    return [
        "qemu-system-x86_64",
        "-machine", "q35,accel=tcg", "-cpu", "max", "-m", "2048", "-smp", "2",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
        "-drive", f"if=virtio,format=raw,snapshot=on,file={disk}",
        "-nic", "none", "-display", "none", "-monitor", "none",
        "-serial", f"file:{serial}", "-boot", "order=c,menu=off", "-no-reboot",
    ]


def menu_verified(serial_bytes: bytes) -> bool:
    output = serial_bytes.decode("utf-8", errors="replace").replace("\r", "\n")
    return (
        BOOT_TEXT in output and MENU_TEXT in output
        and "Recovery could not complete the requested operation" not in output
        and "Kernel panic" not in output
        and "Traceback (most recent call last)" not in output
    )


def fatal_recovery_boot(serial_bytes: bytes) -> bool:
    """Fail promptly on fatal initrd exceptions instead of waiting 8 minutes."""
    output = serial_bytes.decode("utf-8", errors="replace").replace("\r", "\n")
    return any(item in output for item in (
        "can't open file '/usr/libexec/veyra-recovery'",
        "Kernel panic",
        "Traceback (most recent call last)",
        "Recovery could not complete the requested operation",
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uki", type=pathlib.Path, required=True)
    parser.add_argument("--report-dir", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    if not 60 <= args.timeout <= 1200:
        parser.error("--timeout must be between 60 and 1200 seconds")
    if args.report_dir.exists() or args.report_dir.is_symlink():
        parser.error("report directory already exists; refusing to overwrite")
    if not shutil.which("qemu-system-x86_64"):
        parser.error("qemu-system-x86_64 unavailable")
    for utility in ("sfdisk", "mkfs.vfat", "mmd", "mcopy"):
        if not shutil.which(utility):
            parser.error(f"{utility} is required for a temporary ESP")
    try:
        check_uki(args.uki)
        code, vars_template = get_ovmf()
        args.report_dir.mkdir(mode=0o700, parents=True)
        serial = args.report_dir / "serial.log"
        error_log = args.report_dir / "qemu.stderr"
        with tempfile.TemporaryDirectory(prefix="veyra-recovery-qemu-") as temporary:
            root = pathlib.Path(temporary)
            disk = root / "disposable-uefi.img"
            make_esp(disk, args.uki.resolve())
            vars_file = root / "OVMF_VARS.fd"
            shutil.copyfile(vars_template, vars_file)
            command = qemu_command(disk, serial, code, vars_file)
            passed = False
            with error_log.open("wb") as err:
                process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL, stderr=err
                )
                deadline = time.monotonic() + args.timeout
                try:
                    while time.monotonic() < deadline:
                        raw = serial.read_bytes() if serial.exists() else b""
                        if fatal_recovery_boot(raw):
                            break
                        if menu_verified(raw):
                            passed = process.poll() is None
                            break
                        if process.poll() is not None:
                            break
                        time.sleep(2)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
            report = {
                "independent_recovery_uki": str(args.uki),
                "disposable_disk_only": True,
                "offline": True,
                "efi_variables_temporary": True,
                "menu_verified": passed,
                "qemu_exit_after_cleanup": process.returncode,
                "guest_serial": serial.name,
            }
            (args.report_dir / "report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            if not passed:
                tail = serial.read_text(encoding="utf-8", errors="replace")[-7000:] if serial.exists() else "(no serial log)"
                raise RecoveryQemuError(f"Recovery did not reach its menu:\n{tail}")
        print("VEYRA_RECOVERY_INDEPENDENT_QEMU_PASS: Recovery menu appeared without an installed root")
        print("Note: rollback, journal access, and shell actions were not verified")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, RecoveryQemuError) as error:
        print(f"Recovery QEMU smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
