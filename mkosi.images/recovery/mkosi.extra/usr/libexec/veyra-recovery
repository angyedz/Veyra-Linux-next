#!/usr/bin/python3
"""Small, read-mostly Recovery console for a Veyra rescue UKI."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence


class RecoveryError(RuntimeError):
    """An operation cannot be completed safely in the recovery environment."""


_VEYRA_ENTRY = re.compile(r"^veyra-next_[A-Za-z0-9][A-Za-z0-9._+-]*\.efi$")
_BOOT_TRIES = re.compile(r"\+[0-9]+-[0-9]+\.efi$")
_PARTITION_TYPE_EFI = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


def parse_boot_entries(output: str) -> list[dict[str, object]]:
    """Parse and validate the short JSON response from `bootctl list`."""
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RecoveryError("bootctl did not return valid JSON") from exc

    entries: object = decoded
    if isinstance(decoded, dict):
        entries = decoded.get("entries")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise RecoveryError("bootctl did not return an entry list")
    return entries


def previous_boot_candidates(
    entries: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Return Veyra UKIs that systemd-boot has already marked successful."""
    candidates: list[dict[str, object]] = []
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not _VEYRA_ENTRY.fullmatch(identifier):
            continue
        if entry.get("current") is True or _BOOT_TRIES.search(identifier):
            continue
        # A counted entry is still in trial, even if it has not consumed a try.
        if entry.get("tries_left") is not None or entry.get("tries_done") is not None:
            continue
        candidates.append(entry)
    return candidates


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def find_esp_device(run: Callable[..., subprocess.CompletedProcess[str]] = _run) -> str:
    """Find the GPT EFI System Partition without assuming a device name."""
    result = run(["lsblk", "--json", "--output", "PATH,TYPE,PARTTYPE"])
    try:
        block_devices = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RecoveryError("lsblk returned invalid JSON while locating the EFI partition") from exc

    matches: list[str] = []

    def visit(devices: object) -> None:
        if not isinstance(devices, list):
            return
        for device in devices:
            if not isinstance(device, dict):
                continue
            path = device.get("path")
            parttype = device.get("parttype")
            if (
                device.get("type") == "part"
                and isinstance(path, str)
                and isinstance(parttype, str)
                and parttype.lower() == _PARTITION_TYPE_EFI
            ):
                matches.append(path)
            visit(device.get("children"))

    visit(block_devices.get("blockdevices") if isinstance(block_devices, dict) else None)
    if len(matches) != 1:
        raise RecoveryError(f"expected exactly one Veyra EFI partition, found {len(matches)}")
    return matches[0]


def mount_esp(
    run: Callable[..., subprocess.CompletedProcess[str]] = _run,
    mountpoint: pathlib.Path = pathlib.Path("/run/veyra-recovery/esp"),
) -> pathlib.Path:
    device = find_esp_device(run)
    mountpoint.mkdir(parents=True, exist_ok=True)
    run(["mount", "--types", "vfat", "--options", "ro,nodev,nosuid,noexec", device, str(mountpoint)])
    return mountpoint


def list_boot_entries(esp: pathlib.Path, run: Callable[..., subprocess.CompletedProcess[str]] = _run) -> list[dict[str, object]]:
    result = run(["bootctl", f"--esp-path={esp}", "--json=short", "list"])
    return parse_boot_entries(result.stdout)


def list_previous_boots(run: Callable[..., subprocess.CompletedProcess[str]] = _run) -> None:
    """Read-only display of logs from the persistent Veyra /var partition."""
    result = run(["findfs", "PARTLABEL=VEYRA_VAR"], check=False)
    device = result.stdout.strip()
    if result.returncode != 0 or not device.startswith("/dev/"):
        print("Persistent Veyra logs are unavailable: could not find VEYRA_VAR.")
        return
    mountpoint = pathlib.Path("/run/veyra-recovery/var")
    mountpoint.mkdir(parents=True, exist_ok=True)
    # ext4 can replay its journal during a nominally read-only mount unless
    # noload is set. Recovery must never write to persistent system state.
    run(["mount", "--types", "ext4", "--options", "ro,noload,nodev,nosuid,noexec", device, str(mountpoint)])
    journal = mountpoint / "log" / "journal"
    if not journal.is_dir():
        print("No persistent journal directory was found.")
        return
    result = run(["journalctl", f"--directory={journal}", "--list-boots", "--no-pager"], check=False)
    if result.returncode != 0:
        print(result.stderr.strip() or "Could not read the persistent journal.")
    else:
        print(result.stdout.rstrip())


def choose_previous_entry(
    entries: Sequence[dict[str, object]], input_fn: Callable[[str], str] = input,
) -> dict[str, object] | None:
    candidates = previous_boot_candidates(entries)
    if not candidates:
        print("No previously successful Veyra deployment is available.")
        return None
    print("Previously successful Veyra deployments:")
    for index, entry in enumerate(candidates, start=1):
        print(f"  {index}. {entry.get('title') or entry['id']} ({entry['id']})")
    answer = input_fn("Select deployment number, or press Enter to cancel: ").strip()
    if not answer:
        return None
    if not answer.isdecimal() or not 1 <= int(answer) <= len(candidates):
        print("Invalid selection; no boot setting was changed.")
        return None
    return candidates[int(answer) - 1]


def select_previous_deployment(
    esp: pathlib.Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = _run,
    input_fn: Callable[[str], str] = input,
) -> bool:
    entry = choose_previous_entry(list_boot_entries(esp, run), input_fn)
    if entry is None:
        return False
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not _VEYRA_ENTRY.fullmatch(identifier):
        raise RecoveryError("selected boot entry is not a Veyra deployment")
    confirmation = input_fn(f"Type BOOT to start {identifier} once: ").strip()
    if confirmation != "BOOT":
        print("Cancelled; the boot selection was not changed.")
        return False
    run(["bootctl", f"--esp-path={esp}", "set-oneshot", identifier])
    run(["sync"])
    print(f"Next boot is set to {identifier}. Reboot to continue.")
    return True


def recovery_shell() -> None:
    os.execv("/usr/bin/bash", ["bash", "--login"])


def main() -> int:
    print("Veyra Recovery — read-only diagnostics and one-time boot selection")
    try:
        esp = mount_esp()
        while True:
            print("\n1) Show boot entries\n2) Show saved boot logs\n3) Start a previous deployment once\n4) Recovery shell\nq) Power off")
            choice = input("Select an action: ").strip().lower()
            if choice == "1":
                for entry in list_boot_entries(esp):
                    state = "current" if entry.get("current") is True else "available"
                    if entry.get("tries_left") is not None:
                        state = "trial, not confirmed healthy"
                    print(f"{entry.get('title') or entry.get('id')} [{state}]")
            elif choice == "2":
                list_previous_boots()
            elif choice == "3":
                select_previous_deployment(esp)
            elif choice == "4":
                recovery_shell()
            elif choice == "q":
                _run(["poweroff", "--force"])
                return 0
            else:
                print("Choose one of the listed actions.")
    except (OSError, subprocess.CalledProcessError, RecoveryError) as exc:
        print(f"Recovery could not complete the requested operation: {exc}", file=sys.stderr)
        print("No system partition was modified.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
