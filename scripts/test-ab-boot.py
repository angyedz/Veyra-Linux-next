#!/usr/bin/env python3
"""Boot a staged Veyra update and prove the boot-count rollback in QEMU."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def run_privileged(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    if os.geteuid() != 0:
        command = ["sudo", *command]
    return run(command, **kwargs)


def mdir(image: pathlib.Path, esp_offset: int, mdir_bin: str) -> str:
    return run(
        [mdir_bin, "-i", f"{image}@@{esp_offset}", "::/EFI/Linux"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout


def etc_partition(image: pathlib.Path) -> tuple[int, int]:
    table = json.loads(
        run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
    )["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    partitions = [
        partition
        for partition in table["partitions"]
        if partition.get("name") == "VEYRA_ETC"
    ]
    if len(partitions) != 1:
        raise RuntimeError("the staged image must contain exactly one persistent /etc partition")
    partition = partitions[0]
    return int(partition["start"]) * sector_size, int(partition["size"]) * sector_size


def var_partition(image: pathlib.Path) -> tuple[int, int]:
    table = json.loads(
        run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
    )["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    partitions = [
        partition
        for partition in table["partitions"]
        if partition.get("name") == "VEYRA_VAR"
    ]
    if len(partitions) != 1:
        raise RuntimeError("the staged image must contain exactly one persistent /var partition")
    partition = partitions[0]
    return int(partition["start"]) * sector_size, int(partition["size"]) * sector_size


def inject_pending_fallback(image: pathlib.Path, roothash: str, version: str) -> None:
    """Create a disposable same-root/new-version trial binding for QEMU rollback."""
    offset, size = var_partition(image)
    with tempfile.TemporaryDirectory(prefix="veyra-ota-pending-test-") as temporary:
        mountpoint = pathlib.Path(temporary) / "var"
        mountpoint.mkdir()
        run_privileged([
            "mount", "-t", "ext4", "-o",
            f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
            str(image), str(mountpoint),
        ])
        try:
            root = mountpoint / "lib/veyra/vpkg"
            deployments = root / "deployments"
            deployments.mkdir(parents=True, exist_ok=True)
            pending = root / "pending.json"
            binding = deployments / f"{roothash}--{version}.json"
            if pending.exists() or binding.exists():
                raise RuntimeError("the QEMU fallback fixture would overwrite existing vpkg state")
            binding.write_text(
                json.dumps({
                    "schema": 1,
                    "roothash": roothash,
                    "version": version,
                    "generation": None,
                    "requests": [],
                }) + "\n",
                encoding="ascii",
            )
            pending.write_text(
                json.dumps({
                    "schema": 1,
                    "target_roothash": roothash,
                    "version": version,
                }) + "\n",
                encoding="ascii",
            )
        finally:
            run_privileged(["umount", str(mountpoint)])


def inject_journal_test_units(image: pathlib.Path, marker: str, failed_version: str) -> tuple[str, ...]:
    marker_message = f"veyra-ota-failed-trial-{marker}"
    synced_message = f"VEYRA_OTA_MARKER_SYNCED_{marker}"
    check_message = f"VEYRA_OTA_JOURNAL_PERSIST_CI_PASS_{marker}"
    mutation_message = f"VEYRA_OTA_MUTATION_BLOCKED_CI_PASS_{marker}"
    fallback_message = f"VEYRA_OTA_FALLBACK_LOCK_CLEARED_CI_PASS_{marker}"
    offset, size = etc_partition(image)
    with tempfile.TemporaryDirectory(prefix="veyra-ota-journal-test-") as temporary:
        work = pathlib.Path(temporary)
        mountpoint = work / "etc"
        mountpoint.mkdir()
        run_privileged(
            [
                "mount", "-t", "ext4", "-o",
                f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
                str(image), str(mountpoint),
            ]
        )
        try:
            units = {
                "systemd/system/veyra-ota-journal-marker.service": (
                    "[Unit]\n"
                    "Description=Write the failed-trial marker for the Veyra QEMU OTA test\n"
                    "DefaultDependencies=no\n"
                    f"ConditionKernelCommandLine=veyra.ota-journal-marker={marker}\n"
                    "Requires=systemd-journald.service\n"
                    "After=systemd-journald.service\n"
                    "Before=emergency.target\n\n"
                    "[Service]\n"
                    "Type=oneshot\n"
                    f"Environment=VEYRA_OTA_JOURNAL_MARKER={marker_message}\n"
                    "ExecStart=/usr/bin/bash -ec '/usr/bin/systemd-cat -t veyra-ota-journal-ci /usr/bin/echo \"$VEYRA_OTA_JOURNAL_MARKER\"; /usr/bin/journalctl --flush; /usr/bin/journalctl --sync; /usr/bin/echo VEYRA_OTA_MARKER_SYNCED_"
                    f"{marker}'\n"
                    "StandardOutput=journal+console\n"
                    "StandardError=journal+console\n"
                ),
                "systemd/system/veyra-ota-journal-check.service": (
                    "[Unit]\n"
                    "Description=Verify the failed-trial marker in the previous Veyra boot journal\n"
                    "DefaultDependencies=no\n"
                    f"ConditionKernelCommandLine=veyra.ota-journal-check={marker}\n"
                    "Requires=systemd-journald.service\n"
                    "After=systemd-journald.service\n"
                    "Before=boot-complete.target\n\n"
                    "[Service]\n"
                    "Type=oneshot\n"
                    f"Environment=VEYRA_OTA_JOURNAL_MARKER={marker_message}\n"
                    "ExecStart=/usr/bin/bash -o pipefail -ec '/usr/bin/journalctl -b -1 -t veyra-ota-journal-ci --no-pager -o cat | /usr/bin/grep -Fx -- \"$VEYRA_OTA_JOURNAL_MARKER\"'\n"
                    f"ExecStartPost=/usr/bin/echo {check_message}\n"
                    "StandardOutput=journal+console\n"
                    "StandardError=journal+console\n"
                ),
                "systemd/system/veyra-ota-mutation-check.service": (
                    "[Unit]\n"
                    "Description=Verify vpkg mutations are blocked by a pending OTA in QEMU\n"
                    "DefaultDependencies=no\n"
                    f"ConditionKernelCommandLine=veyra.ota-mutation-check={marker}\n"
                    "Requires=veyra-vpkg-sync.service\n"
                    "After=veyra-vpkg-sync.service\n"
                    "Before=emergency.target\n\n"
                    "[Service]\nType=oneshot\n"
                    "ExecStart=/usr/bin/bash -o pipefail -ec '"
                    "/usr/bin/vpkg install htop >/var/lib/veyra/vpkg/ota-mutation.log 2>&1 && exit 1; "
                    "/usr/bin/grep -Fq \"Package changes are blocked while OTA\" /var/lib/veyra/vpkg/ota-mutation.log; "
                    f"/usr/bin/echo {mutation_message}'\n"
                    "StandardOutput=journal+console\nStandardError=journal+console\n"
                ),
                "systemd/system/veyra-ota-fallback-check.service": (
                    "[Unit]\n"
                    "Description=Verify exhausted OTA UKI and package lock were retired in QEMU\n"
                    "DefaultDependencies=no\n"
                    f"ConditionKernelCommandLine=veyra.ota-journal-check={marker}\n"
                    "Requires=veyra-vpkg-sync.service\n"
                    "After=veyra-vpkg-sync.service\n"
                    "Before=boot-complete.target\n\n"
                    "[Service]\nType=oneshot\n"
                    "ExecStart=/usr/bin/bash -ec '"
                    "test ! -e /var/lib/veyra/vpkg/pending.json; "
                    f"test ! -e /boot/EFI/Linux/veyra-next_{failed_version}+0-3.efi; "
                    f"/usr/bin/echo {fallback_message}'\n"
                    "StandardOutput=journal+console\nStandardError=journal+console\n"
                ),
                "systemd/system/emergency.target.d/veyra-ota-journal-ci.conf": (
                    "[Unit]\n"
                    "Requires=veyra-ota-journal-marker.service veyra-ota-mutation-check.service\n"
                    "After=veyra-ota-journal-marker.service veyra-ota-mutation-check.service\n"
                ),
                "systemd/system/boot-complete.target.d/veyra-ota-journal-ci.conf": (
                    "[Unit]\n"
                    "Requires=veyra-ota-journal-check.service\n"
                    "After=veyra-ota-journal-check.service\n"
                    "Requires=veyra-ota-fallback-check.service\n"
                    "After=veyra-ota-fallback-check.service\n"
                ),
            }
            for relative, content in units.items():
                source = work / pathlib.Path(relative).name
                source.write_text(content, encoding="ascii")
                run_privileged(
                    [
                        "install", "-D", "-m", "0644", str(source),
                        str(mountpoint / relative),
                    ]
                )
        finally:
            run_privileged(["umount", str(mountpoint)])
    return synced_message, check_message, mutation_message, fallback_message


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
    accel: str = "tcg",
    cpu: str = "max",
    memory_mib: int = 4096,
    persistent_log_dir: pathlib.Path | None = None,
    ota_port: int = 8765,
) -> str:
    log_path.unlink(missing_ok=True)
    if persistent_log_dir:
        persistent_log_dir.mkdir(parents=True, exist_ok=True)
        (persistent_log_dir / log_path.name).unlink(missing_ok=True)
        log_path.touch()
        os.link(log_path, persistent_log_dir / log_path.name)
    command = [qemu]
    if qemu_data_dir:
        command.extend(["-L", str(qemu_data_dir)])
    command.extend([
        "-machine", f"q35,accel={accel}",
        "-cpu", cpu,
        "-smp", "2",
        "-m", str(memory_mib),
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_path}",
        "-device", "ich9-ahci,id=ahci",
        "-drive", f"if=none,id=systemdisk,format=raw,file={image}",
        "-device", "ide-hd,drive=systemdisk,bus=ahci.0",
        "-nic", f"user,restrict=on,guestfwd=tcp:10.0.2.100:{ota_port}-tcp:127.0.0.1:{ota_port}",
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
            if "VEYRA_VPKG_CI_FAILURE" in normalized:
                excerpt = "\n".join(normalized.splitlines()[-100:])
                raise RuntimeError(
                    "guest Vforge/Vpkg integration test failed before completing:\n"
                    f"{excerpt}"
                )
            if "VEYRA_OTA_BOOTCOUNT_DIAGNOSTICS_FAIL" in normalized:
                excerpt = "\n".join(normalized.splitlines()[-80:])
                raise RuntimeError(
                    "systemd-boot did not expose the selected trial UKI boot-count path:\n"
                    f"{excerpt}"
                )
            if "FAILED (errors=" in normalized or "FAILED (failures=" in normalized:
                excerpt = "\n".join(normalized.splitlines()[-100:])
                raise RuntimeError(
                    "guest Vpkg integration tests reported a failure before OTA staging:\n"
                    f"{excerpt}"
                )
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
    parser.add_argument("--ota-marker", required=True)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--qemu-data-dir", type=pathlib.Path)
    parser.add_argument("--accel", default="tcg")
    parser.add_argument("--cpu", default="max")
    parser.add_argument("--memory-mib", default=4096, type=int)
    parser.add_argument("--log-directory", type=pathlib.Path)
    parser.add_argument("--ovmf-code", required=True, type=pathlib.Path)
    parser.add_argument("--ovmf-vars", required=True, type=pathlib.Path)
    parser.add_argument("--boot-timeout", default=300, type=int)
    parser.add_argument("--ota-port", default=8765, type=int)
    parser.add_argument("--expect-vpkg-test", action="store_true")
    parser.add_argument("--mcopy", default="mcopy")
    parser.add_argument("--mdir", default="mdir")
    args = parser.parse_args()

    try:
        if args.memory_mib < 4096:
            raise RuntimeError("OTA boots require at least 4096 MiB for persistent /home growth.")
        if not shutil.which(args.qemu):
            raise RuntimeError(f"QEMU executable not found: {args.qemu}")
        if not shutil.which(args.mcopy) or not shutil.which(args.mdir):
            raise RuntimeError("mtools mcopy and mdir are required for the boot-count test")
        image = args.image.resolve()
        if not image.is_file():
            raise RuntimeError(f"missing staged test image: {image}")
        code, pristine_vars = find_ovmf_pair(args.ovmf_code, args.ovmf_vars)
        partitions = run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
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
                "VEYRA_OTA_BOOTCOUNT_DIAGNOSTICS",
                f"VEYRA_OTA_MANUAL_OLD_CI_PASS_{args.ota_marker}",
                f"VEYRA_OTA_SUCCESS_CI_PASS_{args.ota_marker}",
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
                accel=args.accel,
                cpu=args.cpu,
                memory_mib=args.memory_mib,
                persistent_log_dir=args.log_directory,
                ota_port=args.ota_port,
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
            journal_marker = uuid.uuid4().hex
            synced_message, check_message, mutation_message, fallback_message = inject_journal_test_units(
                test_image, journal_marker, failed_version
            )
            checked_uki = work / f"veyra-next_{args.version}-journal-check.efi"
            # Only the previously blessed UKI verifies the failed boot's journal.
            helper.patch_uki(
                source_uki,
                checked_uki,
                args.version,
                args.roothash,
                "graphical.target",
                extra_cmdline=(f"veyra.ota-journal-check={journal_marker}",),
            )
            run([
                args.mcopy, "-o", "-i", f"{test_image}@@{esp_offset}",
                str(checked_uki), f"::/EFI/Linux/{stable_entry}",
            ])
            helper.patch_uki(
                source_uki,
                failed_uki,
                failed_version,
                args.roothash,
                "emergency.target",
                version_id="44.1",
                extra_cmdline=(
                    f"veyra.ota-journal-marker={journal_marker}",
                    f"veyra.ota-mutation-check={journal_marker}",
                ),
            )
            run([
                args.mcopy, "-o", "-i", f"{test_image}@@{esp_offset}",
                str(failed_uki), f"::/EFI/Linux/{failed_uki.name}",
            ])
            inject_pending_fallback(test_image, args.roothash, failed_version)

            for attempt, expected_tries in enumerate(("+2-1", "+1-2", "+0-3"), start=1):
                failed_log = work / f"failed-c-{attempt}.log"
                boot_until(
                    image=test_image,
                    log_path=failed_log,
                    code=code,
                    vars_path=vars_path,
                    qemu=args.qemu,
                    qemu_data_dir=args.qemu_data_dir,
                    expected=(
                        "Reached target emergency.target",
                        "You are in emergency mode.",
                        synced_message,
                        mutation_message,
                    ),
                    timeout=args.boot_timeout,
                    accel=args.accel,
                    cpu=args.cpu,
                    memory_mib=args.memory_mib,
                    persistent_log_dir=args.log_directory,
                    ota_port=args.ota_port,
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
                    check_message,
                    fallback_message,
                ),
                timeout=args.boot_timeout,
                accel=args.accel,
                cpu=args.cpu,
                memory_mib=args.memory_mib,
                persistent_log_dir=args.log_directory,
                ota_port=args.ota_port,
            )
            if "Reached target emergency.target" in rollback:
                raise RuntimeError("systemd-boot selected the exhausted candidate instead of the good slot")
            print(f"After three failed boots, systemd-boot returned to Veyra {args.version}.", flush=True)
            print("The fallback boot read the failed-trial marker from the previous-boot journal.", flush=True)

        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(str(detail), file=sys.stderr)
        print(f"test-ab-boot: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
