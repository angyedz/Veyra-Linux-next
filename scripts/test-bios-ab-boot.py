#!/usr/bin/env python3
"""Prove BIOS OTA staging, GRUB blessing, and failed-trial fallback in QEMU."""

from __future__ import annotations

import argparse
import importlib.util
import json
from importlib.machinery import SourceFileLoader
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOT_TEST = pathlib.Path(__file__).with_name("test-ab-boot.py")
BOOT_SPEC = importlib.util.spec_from_loader(
    "veyra_ab_boot", SourceFileLoader("veyra_ab_boot", str(BOOT_TEST))
)
if BOOT_SPEC is None or BOOT_SPEC.loader is None:
    raise RuntimeError("could not load the shared QEMU boot helper")
boot = importlib.util.module_from_spec(BOOT_SPEC)
BOOT_SPEC.loader.exec_module(boot)

GRUB_OTA = ROOT / "mkosi.extra/usr/lib/veyra/grub_ota.py"
GRUB_SPEC = importlib.util.spec_from_loader(
    "veyra_grub_ota", SourceFileLoader("veyra_grub_ota", str(GRUB_OTA))
)
if GRUB_SPEC is None or GRUB_SPEC.loader is None:
    raise RuntimeError("could not load the GRUB OTA helper")
grub_ota = importlib.util.module_from_spec(GRUB_SPEC)
GRUB_SPEC.loader.exec_module(grub_ota)

ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
VAR_LABEL = "VEYRA_VAR"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def partition_range(image: pathlib.Path, *, label: str | None = None, part_type: str | None = None) -> tuple[int, int]:
    table = json.loads(run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout)["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    matches = [
        partition
        for partition in table["partitions"]
        if (label is None or partition.get("name") == label)
        and (part_type is None or str(partition.get("type", "")).lower() == part_type)
    ]
    if len(matches) != 1:
        identity = label or part_type
        raise RuntimeError(f"expected exactly one image partition for {identity}, found {len(matches)}")
    return int(matches[0]["start"]) * sector_size, int(matches[0]["size"]) * sector_size


def mount_image_partition(
    image: pathlib.Path, mountpoint: pathlib.Path, *, filesystem: str, label: str | None = None,
    part_type: str | None = None,
) -> None:
    offset, size = partition_range(image, label=label, part_type=part_type)
    boot.run_privileged([
        "mount", "-t", filesystem, "-o",
        f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
        str(image), str(mountpoint),
    ])


def with_mounted_partition(
    image: pathlib.Path,
    *,
    filesystem: str,
    label: str | None = None,
    part_type: str | None = None,
    callback,
):
    with tempfile.TemporaryDirectory(prefix="veyra-bios-ota-partition-") as temporary:
        mountpoint = pathlib.Path(temporary) / "partition"
        mountpoint.mkdir()
        mount_image_partition(
            image, mountpoint, filesystem=filesystem, label=label, part_type=part_type
        )
        try:
            return callback(mountpoint)
        finally:
            boot.run_privileged(["umount", str(mountpoint)])


def read_grub_state(image: pathlib.Path) -> dict[str, str]:
    return with_mounted_partition(
        image,
        filesystem="vfat",
        part_type=ESP_TYPE,
        callback=grub_ota.read_state,
    )


def read_pending(image: pathlib.Path) -> dict[str, object] | None:
    def inspect(mountpoint: pathlib.Path) -> dict[str, object] | None:
        path = mountpoint / "lib/veyra/vpkg/pending.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    return with_mounted_partition(
        image, filesystem="ext4", label=VAR_LABEL, callback=inspect
    )


def next_version(version: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise RuntimeError(f"BIOS rollback test requires a numeric image version, got {version!r}")
    components = version.split(".")
    components[-1] = str(int(components[-1]) + 1)
    return ".".join(components)


def stage_synthetic_failed_trial(
    image: pathlib.Path, *, stable_version: str, stable_roothash: str
) -> tuple[str, str]:
    failed_version = next_version(stable_version)
    failed_roothash = "f" * 64
    if failed_roothash == stable_roothash:
        failed_roothash = "e" * 64

    def stage(boot_dir: pathlib.Path) -> None:
        assets = boot_dir / "VEYRA"
        assets.mkdir(parents=True, exist_ok=True)
        kernel = assets / f"veyra-next_{failed_version}.vmlinuz"
        initrd = assets / f"veyra-next_{failed_version}.initrd.zst"
        kernel.write_bytes(b"invalid BIOS trial kernel for fallback test\n")
        initrd.write_bytes(b"invalid BIOS trial initramfs for fallback test\n")
        grub_ota.stage_trial(
            boot_dir,
            current_version=stable_version,
            current_roothash=stable_roothash,
            target_version=failed_version,
            target_roothash=failed_roothash,
        )

    with_mounted_partition(
        image, filesystem="vfat", part_type=ESP_TYPE, callback=stage
    )
    boot.inject_pending_fallback(image, failed_roothash, failed_version)
    return failed_version, failed_roothash


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
    parser.add_argument("--boot-timeout", default=600, type=int)
    parser.add_argument("--ota-port", default=8765, type=int)
    parser.add_argument("--expect-vpkg-test", action="store_true")
    args = parser.parse_args()

    try:
        if args.memory_mib < 4096:
            raise RuntimeError("BIOS OTA boots require at least 4096 MiB for persistent /home growth")
        if not shutil.which(args.qemu):
            raise RuntimeError(f"QEMU executable not found: {args.qemu}")
        if not grub_ota.find_grub_editenv():
            raise RuntimeError("grub-editenv is required for BIOS OTA verification")
        image = args.image.resolve()
        if not image.is_file():
            raise RuntimeError(f"missing staged BIOS image: {image}")

        with tempfile.TemporaryDirectory(prefix="veyra-bios-ota-boot-") as temporary:
            work = pathlib.Path(temporary)
            stable_marker = f"VEYRA_BIOS_OTA_STABLE_BOOT_CI_PASS_{args.ota_marker}"
            successful_markers = [
                f"VEYRA_BIOS_OTA_PREPARE_CI_PASS_{args.ota_marker}",
                f"roothash={args.roothash}",
                f"veyra.image_version={args.version}",
                stable_marker,
            ]
            if args.expect_vpkg_test:
                successful_markers.append("VEYRA_VPKG_CI_PASS")
            successful_log = boot.boot_until(
                image=image,
                log_path=work / "successful-ota.log",
                code=None,
                vars_path=None,
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
                raise RuntimeError("the successful BIOS trial reported a dm-verity integrity failure")

            state = read_grub_state(image)
            if (
                state.get("veyra_stable_version") != args.version
                or state.get("veyra_stable_roothash") != args.roothash
                or any(key in state for key in grub_ota.TRIAL_KEYS)
            ):
                raise RuntimeError(f"successful BIOS OTA did not durably bless GRUB state: {state!r}")
            if read_pending(image) is not None:
                raise RuntimeError("successful BIOS OTA left the vpkg pending lock on disk")
            print(f"SeaBIOS installed and blessed signed Veyra update {args.version}.", flush=True)

            failed_version, failed_roothash = stage_synthetic_failed_trial(
                image, stable_version=args.version, stable_roothash=args.roothash
            )
            for attempt, expected_tries in enumerate(("2", "1", "0"), start=1):
                log_path = work / f"fallback-{attempt}.log"
                log = boot.boot_until(
                    image=image,
                    log_path=log_path,
                    code=None,
                    vars_path=None,
                    qemu=args.qemu,
                    qemu_data_dir=args.qemu_data_dir,
                    expected=(
                        f"roothash={args.roothash}",
                        f"veyra.image_version={args.version}",
                        stable_marker,
                    ),
                    timeout=args.boot_timeout,
                    accel=args.accel,
                    cpu=args.cpu,
                    memory_mib=args.memory_mib,
                    persistent_log_dir=args.log_directory,
                    ota_port=args.ota_port,
                )
                if f"roothash={failed_roothash}" in log:
                    raise RuntimeError("GRUB started the synthetic failed BIOS trial")
                state = read_grub_state(image)
                pending = read_pending(image)
                if attempt < 3:
                    if (
                        state.get("veyra_trial_version") != failed_version
                        or state.get("veyra_trial_roothash") != failed_roothash
                        or state.get("veyra_trial_tries_left") != expected_tries
                        or pending is None
                    ):
                        raise RuntimeError(
                            f"BIOS trial attempt {attempt} did not preserve the pending fallback state"
                        )
                elif (
                    any(key in state for key in grub_ota.TRIAL_KEYS)
                    or pending is not None
                ):
                    raise RuntimeError("three failed BIOS trial attempts did not clear GRUB and vpkg state")
                print(f"GRUB fallback attempt {attempt}/3 left {expected_tries} tries.", flush=True)

        print(f"After three failed BIOS trial attempts, Veyra {args.version} remained bootable.", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(str(detail), file=sys.stderr)
        print(f"test-bios-ab-boot: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
