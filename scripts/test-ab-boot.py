#!/usr/bin/env python3
"""Boot a staged Veyra update and prove the boot-count rollback in QEMU."""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def mdir(image: pathlib.Path, esp_offset: int, mdir_bin: str) -> str:
    return run(
        [mdir_bin, "-i", f"{image}@@{esp_offset}", "::/EFI/Linux"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout


def stop_vm(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def boot_until(
    *,
    image: pathlib.Path,
    log_path: pathlib.Path,
    code: pathlib.Path,
    vars_path: pathlib.Path,
    qemu: str,
    qemu_data_dir: pathlib.Path | None,
    expected: tuple[str, ...],
    timeout: int,
) -> str:
    log_path.unlink(missing_ok=True)
    command = [qemu]
    if qemu_data_dir:
        command.extend(["-L", str(qemu_data_dir)])
    command.extend([
        "-machine", "q35,accel=tcg",
        "-cpu", "max",
        "-smp", "2",
        "-m", "2048",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_path}",
        "-device", "ich9-ahci,id=ahci",
        "-drive", f"if=none,id=systemdisk,format=raw,file={image}",
        "-device", "ide-hd,drive=systemdisk,bus=ahci.0",
        "-nic", "none",
        "-display", "none",
        "-serial", f"file:{log_path}",
        "-monitor", "none",
    ])
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                log = log_path.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                log = ""
            normalized = normalize_log(log)
            if all(marker in normalized for marker in expected):
                return normalized
            time.sleep(2)
        try:
            log = log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            log = ""
        normalized = normalize_log(log)
        missing = [marker for marker in expected if marker not in normalized]
        excerpt = "\n".join(normalized.splitlines()[-80:])
        raise RuntimeError(
            f"QEMU did not reach {missing!r} within {timeout}s; last serial output:\n{excerpt}"
        )
    finally:
        stop_vm(process)


def normalize_log(log: str) -> str:
    log = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    log = re.sub(r"\x1b\][^\x07]*?(?:\x07|\x1b\\)", "", log)
    return log.replace("\r", "\n")


def find_ovmf_pair(code: pathlib.Path, variables: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    code = code.resolve()
    variables = variables.resolve()
    for path in (code, variables):
        if not path.is_file():
            raise RuntimeError(f"missing OVMF firmware file: {path}")
    return code, variables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--roothash", required=True)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--qemu-data-dir", type=pathlib.Path)
    parser.add_argument("--ovmf-code", required=True, type=pathlib.Path)
    parser.add_argument("--ovmf-vars", required=True, type=pathlib.Path)
    parser.add_argument("--boot-timeout", default=300, type=int)
    parser.add_argument("--expect-vpkg-test", action="store_true")
    parser.add_argument("--mcopy", default="mcopy")
    parser.add_argument("--mdir", default="mdir")
    args = parser.parse_args()

    try:
        if not shutil.which(args.qemu):
            raise RuntimeError(f"QEMU executable not found: {args.qemu}")
        if not shutil.which(args.mcopy) or not shutil.which(args.mdir):
            raise RuntimeError("mtools mcopy and mdir are required for the boot-count test")
        image = args.image.resolve()
        if not image.is_file():
            raise RuntimeError(f"missing staged test image: {image}")
        code, pristine_vars = find_ovmf_pair(args.ovmf_code, args.ovmf_vars)
        partitions = run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
        import json

        layout = json.loads(partitions)["partitiontable"]
        sector_size = int(layout.get("sectorsize", 512))
        esp_type = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        esps = [p for p in layout["partitions"] if str(p.get("type", "")).lower() == esp_type]
        if len(esps) != 1:
            raise RuntimeError("the staged image must contain exactly one EFI system partition")
        esp_offset = int(esps[0]["start"]) * sector_size

        with tempfile.TemporaryDirectory(prefix="veyra-ab-boot-") as temporary:
            work = pathlib.Path(temporary)
            test_image = image
            vars_path = work / "OVMF_VARS.fd"
            shutil.copyfile(pristine_vars, vars_path)

            boot_log = work / "boot-b.log"
            successful_markers = [
                f"roothash={args.roothash}",
                "Reached target graphical.target",
                "VEYRA_BOOT_BLESS_CI_PASS",
            ]
            if args.expect_vpkg_test:
                successful_markers.append("VEYRA_VPKG_CI_PASS")
            successful_log = boot_until(
                image=test_image,
                log_path=boot_log,
                code=code,
                vars_path=vars_path,
                qemu=args.qemu,
                qemu_data_dir=args.qemu_data_dir,
                expected=tuple(successful_markers),
                timeout=args.boot_timeout,
            )
            if "device-mapper: verity" in successful_log or "metadata block" in successful_log:
                raise RuntimeError("the successful trial boot reported a dm-verity integrity failure")
            stable_entry = f"veyra-next_{args.version}.efi"
            listing = mdir(test_image, esp_offset, args.mdir)
            if stable_entry.lower() not in listing.lower():
                raise RuntimeError("systemd-boot did not bless the successful trial UKI")
            print(f"Booted signed update {args.version} from the inactive slot and observed dm-verity.", flush=True)
            print("systemd reached graphical.target, completed boot health checks, and blessed the UKI.", flush=True)
            if args.expect_vpkg_test:
                print("The signed vpkg install, upgrade and remove test passed inside Veyra.", flush=True)

            # Add a higher-priority emergency UKI to simulate the next update's
            # application failing during boot. Its version and roothash refer to
            # the already-good B slot; only its requested systemd target fails.
            failed_version_parts = args.version.rsplit(".", 1)
            if len(failed_version_parts) != 2 or not failed_version_parts[1].isdigit():
                raise RuntimeError(f"rollback test requires a dotted numeric version: {args.version}")
            failed_version = f"{failed_version_parts[0]}.{int(failed_version_parts[1]) + 1}"
            existing_entries = mdir(test_image, esp_offset, args.mdir).lower()
            while f"veyra-next_{failed_version}" in existing_entries:
                failed_parts = failed_version.rsplit(".", 1)
                failed_version = f"{failed_parts[0]}.{int(failed_parts[1]) + 1}"
            failed_uki = work / f"veyra-next_{failed_version}+3-0.efi"
            source_uki = work / f"veyra-next_{args.version}.efi"
            # Extract the already staged, blessed B UKI so the deliberate test
            # candidate keeps its exact boot artifacts and root hash.
            run([
                args.mcopy, "-o", "-i", f"{test_image}@@{esp_offset}",
                f"::/EFI/Linux/{stable_entry}", str(source_uki),
            ])
            from importlib.machinery import SourceFileLoader
            import importlib.util

            helper_path = pathlib.Path(__file__).with_name("test-sysupdate-image.py")
            spec = importlib.util.spec_from_loader(
                "veyra_sysupdate_image", SourceFileLoader("veyra_sysupdate_image", str(helper_path))
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load the UKI fixture helper")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            helper.patch_uki(
                source_uki,
                failed_uki,
                failed_version,
                args.roothash,
                "emergency.target",
                version_id="44.1",
            )
            run([
                args.mcopy, "-o", "-i", f"{test_image}@@{esp_offset}",
                str(failed_uki), f"::/EFI/Linux/{failed_uki.name}",
            ])

            for attempt, expected_tries in enumerate(("+2-1", "+1-2", "+0-3"), start=1):
                failed_log = work / f"failed-c-{attempt}.log"
                boot_until(
                    image=test_image,
                    log_path=failed_log,
                    code=code,
                    vars_path=vars_path,
                    qemu=args.qemu,
                    qemu_data_dir=args.qemu_data_dir,
                    expected=("Reached target emergency.target", "You are in emergency mode."),
                    timeout=args.boot_timeout,
                )
                listing = mdir(test_image, esp_offset, args.mdir)
                expected_name = f"veyra-next_{failed_version}{expected_tries}.efi"
                if expected_name.lower() not in listing.lower():
                    raise RuntimeError(
                        f"failed boot {attempt} did not decrement its boot counter to {expected_name}"
                    )
                print(f"Failed trial boot {attempt}/3 left the expected counter {expected_tries}.", flush=True)

            fallback_log = work / "rollback.log"
            rollback = boot_until(
                image=test_image,
                log_path=fallback_log,
                code=code,
                vars_path=vars_path,
                qemu=args.qemu,
                qemu_data_dir=args.qemu_data_dir,
                expected=(
                    f"roothash={args.roothash}",
                    "Reached target graphical.target",
                ),
                timeout=args.boot_timeout,
            )
            if "Reached target emergency.target" in rollback:
                raise RuntimeError("systemd-boot selected the exhausted candidate instead of the good slot")
            print(f"After three failed boots, systemd-boot returned to Veyra {args.version}.", flush=True)

        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(str(detail), file=sys.stderr)
        print(f"test-ab-boot: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
