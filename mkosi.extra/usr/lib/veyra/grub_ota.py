#!/usr/bin/python3
"""Manage Veyra's GRUB trial state for Legacy BIOS A/B updates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
ROOTHASH_RE = re.compile(r"^[0-9a-f]{64}$")
TRIAL_KEYS = ("veyra_trial_version", "veyra_trial_roothash", "veyra_trial_tries_left")
STABLE_KEYS = ("veyra_stable_version", "veyra_stable_roothash")
TRIES = 3
FEDORA_RELEASE = "44"


def validate_identity(version: str, roothash: str) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid deployment version: {version!r}")
    if not ROOTHASH_RE.fullmatch(roothash):
        raise ValueError("invalid deployment roothash")


def running_bios_identity(
    os_release: Path = Path("/usr/lib/os-release"),
    cmdline: Path = Path("/proc/cmdline"),
) -> tuple[str, str]:
    try:
        release_lines = os_release.read_text(encoding="utf-8").splitlines()
        command_tokens = cmdline.read_text(encoding="ascii").split()
    except (OSError, UnicodeError) as error:
        raise RuntimeError("could not read the running Legacy BIOS deployment identity") from error
    fields: dict[str, list[str]] = {"ID": [], "VERSION_ID": [], "IMAGE_ID": [], "IMAGE_VERSION": []}
    for line in release_lines:
        key, separator, value = line.partition("=")
        if separator and key in fields:
            fields[key].append(value.strip().strip('"'))
    if any(len(fields[key]) != 1 for key in fields):
        raise RuntimeError("the BIOS root must contain exactly one complete Veyra image identity")
    if (
        fields["ID"][0] != "fedora"
        or fields["VERSION_ID"][0] != FEDORA_RELEASE
        or fields["IMAGE_ID"][0] != "veyra-next"
    ):
        raise RuntimeError("the running BIOS root is not a Fedora 44 Veyra deployment")
    versions = [token.partition("=")[2] for token in command_tokens if token.startswith("veyra.image_version=")]
    roothashes = [token.partition("=")[2] for token in command_tokens if token.startswith("roothash=")]
    version = fields["IMAGE_VERSION"][0]
    if versions != [version]:
        raise RuntimeError("the BIOS kernel version does not match the verity-protected root")
    if len(roothashes) != 1:
        raise RuntimeError("the BIOS kernel command line must contain exactly one roothash")
    roothash = roothashes[0]
    validate_identity(version, roothash)
    return version, roothash


def find_grub_editenv() -> str | None:
    return shutil.which("grub2-editenv") or shutil.which("grub-editenv")


def grub_editenv() -> str:
    command = find_grub_editenv()
    if command is None:
        raise RuntimeError("grub2-editenv is required to manage Legacy BIOS update state")
    return command


def run_editenv(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if path.is_symlink():
        raise RuntimeError(f"refusing symlinked GRUB environment block: {path}")
    return subprocess.run(
        [grub_editenv(), str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def sync_path(path: Path) -> None:
    subprocess.run(["sync", "-f", str(path)], check=True)


def read_environment(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"GRUB environment block is missing or unsafe: {path}")
    result = run_editenv(path, "list")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in values:
            raise RuntimeError("GRUB environment block contains malformed or duplicate state")
        values[key] = value
    return values


def set_environment(path: Path, values: dict[str, str]) -> None:
    if not values:
        return
    run_editenv(path, "set", *(f"{key}={value}" for key, value in values.items()))
    actual = read_environment(path)
    if any(actual.get(key) != value for key, value in values.items()):
        raise RuntimeError("GRUB did not persist the requested boot state")


def replace_environment(
    path: Path,
    *,
    values: dict[str, str] | None = None,
    unset: tuple[str, ...] = (),
) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"GRUB environment block is missing or unsafe: {path}")
    updated = read_environment(path)
    updated.update(values or {})
    for key in unset:
        updated.pop(key, None)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".grubenv.", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.unlink()
        run_editenv(temporary_path, "create")
        if updated:
            run_editenv(
                temporary_path,
                "set",
                *(f"{key}={value}" for key, value in updated.items()),
            )
        actual = read_environment(temporary_path)
        if actual != updated:
            raise RuntimeError("the replacement GRUB environment block did not verify")
        os.chmod(temporary_path, path.stat().st_mode & 0o777)
        sync_path(temporary_path)
        os.replace(temporary_path, path)
        sync_path(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def initialize_grubenv(path: Path, stable_version: str, stable_roothash: str) -> None:
    validate_identity(stable_version, stable_roothash)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to replace existing GRUB environment block: {path}")
    run_editenv(path, "create")
    set_environment(
        path,
        {
            "veyra_stable_version": stable_version,
            "veyra_stable_roothash": stable_roothash,
        },
    )
    sync_path(path)


def render_grub_config(fallback_version: str, fallback_roothash: str) -> str:
    validate_identity(fallback_version, fallback_roothash)
    return f"""insmod part_gpt
insmod fat
insmod search
insmod search_fs_file
search --no-floppy --file --set=root /grub2/grub.cfg
insmod loadenv
insmod normal
insmod linux
set timeout=0
set veyra_stable_version={fallback_version}
set veyra_stable_roothash={fallback_roothash}
load_env --file=/grub2/grubenv
if [ -z "$veyra_stable_version" ]; then
    set veyra_stable_version={fallback_version}
fi
if [ -z "$veyra_stable_roothash" ]; then
    set veyra_stable_roothash={fallback_roothash}
fi
set default=veyra-stable
if [ -n "$veyra_trial_version" ]; then
    if [ "$veyra_trial_tries_left" -gt 0 ]; then
        set default=veyra-trial
    fi
fi
function veyra_boot_stable {{
    if linux /VEYRA/veyra-next_${{veyra_stable_version}}.vmlinuz roothash=${{veyra_stable_roothash}} veyra.image_version=${{veyra_stable_version}} systemd.firstboot=no module.sig_enforce=1 systemd.unit=graphical.target; then
        if initrd /VEYRA/veyra-next_${{veyra_stable_version}}.initrd; then
            return
        fi
    fi
    echo "Could not load the stable Veyra deployment."
}}
function veyra_boot_trial {{
    if [ "$veyra_trial_tries_left" = "3" ]; then
        set veyra_trial_tries_left=2
    elif [ "$veyra_trial_tries_left" = "2" ]; then
        set veyra_trial_tries_left=1
    elif [ "$veyra_trial_tries_left" = "1" ]; then
        set veyra_trial_tries_left=0
    else
        echo "No Veyra trial attempts remain; booting the stable deployment."
        veyra_boot_stable
        return
    fi
    if save_env --file=/grub2/grubenv veyra_trial_tries_left; then
        if linux /VEYRA/veyra-next_${{veyra_trial_version}}.vmlinuz roothash=${{veyra_trial_roothash}} veyra.image_version=${{veyra_trial_version}} systemd.firstboot=no module.sig_enforce=1 systemd.unit=graphical.target; then
            if initrd /VEYRA/veyra-next_${{veyra_trial_version}}.initrd; then
                return
            fi
        fi
        echo "Could not load the Veyra trial; booting the stable deployment."
        veyra_boot_stable
    else
        echo "Could not record this Veyra trial boot; booting the stable deployment."
        veyra_boot_stable
    fi
}}
menuentry "Veyra Next stable" --id=veyra-stable {{
    veyra_boot_stable
}}
if [ -n "$veyra_trial_version" ]; then
    if [ "$veyra_trial_tries_left" -gt 0 ]; then
        menuentry "Veyra Next trial (${{veyra_trial_version}})" --id=veyra-trial {{
            veyra_boot_trial
        }}
    fi
fi
"""


def environment_path(boot_dir: Path) -> Path:
    return boot_dir / "grub2/grubenv"


def read_state(boot_dir: Path) -> dict[str, str]:
    values = read_environment(environment_path(boot_dir))
    if any(key not in values for key in STABLE_KEYS):
        raise RuntimeError("GRUB environment block has no valid stable deployment")
    validate_identity(values["veyra_stable_version"], values["veyra_stable_roothash"])
    present_trial = [key in values for key in TRIAL_KEYS]
    if any(present_trial) and not all(present_trial):
        raise RuntimeError("GRUB environment block has incomplete trial state")
    if all(present_trial):
        validate_identity(values["veyra_trial_version"], values["veyra_trial_roothash"])
        if values["veyra_trial_version"] == values["veyra_stable_version"]:
            raise RuntimeError("GRUB trial version must differ from the stable deployment")
        try:
            tries_left = int(values["veyra_trial_tries_left"])
        except ValueError as error:
            raise RuntimeError("GRUB trial attempt counter is invalid") from error
        if not 0 <= tries_left <= TRIES:
            raise RuntimeError("GRUB trial attempt counter is out of range")
    return values


def regular_asset(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def stage_trial(
    boot_dir: Path,
    *,
    current_version: str,
    current_roothash: str,
    target_version: str,
    target_roothash: str,
) -> None:
    validate_identity(current_version, current_roothash)
    validate_identity(target_version, target_roothash)
    state = read_state(boot_dir)
    if (
        state["veyra_stable_version"] != current_version
        or state["veyra_stable_roothash"] != current_roothash
    ):
        raise RuntimeError("running deployment does not match GRUB's stable deployment")
    if any(key in state for key in TRIAL_KEYS):
        raise RuntimeError("a GRUB trial is already pending")
    if target_version == current_version:
        raise RuntimeError("the GRUB trial must use a new deployment version")
    assets_dir = boot_dir / "VEYRA"
    for suffix in ("vmlinuz", "initrd"):
        asset = assets_dir / f"veyra-next_{target_version}.{suffix}"
        if not regular_asset(asset):
            raise RuntimeError(f"missing or unsafe BIOS boot asset: {asset}")
    replace_environment(
        environment_path(boot_dir),
        values={
            "veyra_trial_version": target_version,
            "veyra_trial_roothash": target_roothash,
            "veyra_trial_tries_left": str(TRIES),
        },
    )
    sync_path(environment_path(boot_dir))


def write_fallback_config(boot_dir: Path, version: str, roothash: str) -> None:
    config = boot_dir / "grub2/grub.cfg"
    if not config.is_file() or config.is_symlink():
        raise RuntimeError("the Veyra GRUB configuration is missing or unsafe")
    content = render_grub_config(version, roothash)
    mode = config.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="ascii", dir=config.parent, prefix=".grub.cfg.", delete=False
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, config)
        sync_path(config)
    finally:
        temporary_path.unlink(missing_ok=True)


def bless_trial(boot_dir: Path, current_version: str, current_roothash: str) -> None:
    validate_identity(current_version, current_roothash)
    state = read_state(boot_dir)
    if not any(key in state for key in TRIAL_KEYS):
        if (
            state["veyra_stable_version"] == current_version
            and state["veyra_stable_roothash"] == current_roothash
        ):
            # A previous boot may have durably blessed GRUB but failed while
            # committing the matching vpkg generation. Let that commit retry.
            write_fallback_config(boot_dir, current_version, current_roothash)
            return
        raise RuntimeError("running deployment does not match the pending GRUB trial")
    if (
        state.get("veyra_trial_version") != current_version
        or state.get("veyra_trial_roothash") != current_roothash
    ):
        raise RuntimeError("running deployment does not match the pending GRUB trial")
    write_fallback_config(boot_dir, current_version, current_roothash)
    env_path = environment_path(boot_dir)
    replace_environment(
        env_path,
        values={
            "veyra_stable_version": current_version,
            "veyra_stable_roothash": current_roothash,
        },
        unset=TRIAL_KEYS,
    )


def cancel_trial(
    boot_dir: Path,
    *,
    current_version: str,
    current_roothash: str,
    target_version: str,
    target_roothash: str,
) -> None:
    validate_identity(current_version, current_roothash)
    validate_identity(target_version, target_roothash)
    state = read_state(boot_dir)
    if not any(key in state for key in TRIAL_KEYS):
        if current_version == target_version and current_roothash == target_roothash:
            raise RuntimeError("the pending GRUB trial is currently running")
        return
    if (
        state.get("veyra_trial_version") != target_version
        or state.get("veyra_trial_roothash") != target_roothash
    ):
        raise RuntimeError("pending GRUB trial does not match the requested cancellation")
    if current_version == target_version and current_roothash == target_roothash:
        raise RuntimeError("the pending GRUB trial is currently running")
    env_path = environment_path(boot_dir)
    replace_environment(env_path, unset=TRIAL_KEYS)


def finish_fallback(
    boot_dir: Path,
    *,
    current_version: str,
    current_roothash: str,
    target_version: str,
    target_roothash: str,
) -> bool:
    validate_identity(current_version, current_roothash)
    validate_identity(target_version, target_roothash)
    state = read_state(boot_dir)
    if current_version == target_version and current_roothash == target_roothash:
        return False
    if (
        state["veyra_stable_version"] != current_version
        or state["veyra_stable_roothash"] != current_roothash
    ):
        return False
    has_trial = any(key in state for key in TRIAL_KEYS)
    if has_trial and (
        state.get("veyra_trial_version") != target_version
        or state.get("veyra_trial_roothash") != target_roothash
        or state["veyra_trial_tries_left"] != "0"
    ):
        return False
    assets_dir = boot_dir / "VEYRA"
    for suffix in ("vmlinuz", "initrd"):
        asset = assets_dir / f"veyra-next_{target_version}.{suffix}"
        if asset.is_symlink() or (asset.exists() and not asset.is_file()):
            raise RuntimeError(f"refusing to retire an unsafe BIOS boot asset: {asset}")
    for suffix in ("vmlinuz", "initrd"):
        (assets_dir / f"veyra-next_{target_version}.{suffix}").unlink(missing_ok=True)
    sync_path(assets_dir)
    env_path = environment_path(boot_dir)
    replace_environment(env_path, unset=TRIAL_KEYS)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("identity", "status", "stage", "bless", "cancel", "settle"))
    parser.add_argument("--boot-dir", type=Path, default=Path("/boot"))
    parser.add_argument("--current-version")
    parser.add_argument("--current-roothash")
    parser.add_argument("--target-version")
    parser.add_argument("--target-roothash")
    args = parser.parse_args()
    required = {
        "identity": (),
        "status": (),
        "stage": ("current_version", "current_roothash", "target_version", "target_roothash"),
        "bless": ("current_version", "current_roothash"),
        "cancel": ("current_version", "current_roothash", "target_version", "target_roothash"),
        "settle": ("current_version", "current_roothash", "target_version", "target_roothash"),
    }[args.command]
    missing = [name.replace("_", "-") for name in required if getattr(args, name) is None]
    if missing:
        parser.error(f"{args.command} requires: {', '.join('--' + name for name in missing)}")
    try:
        if args.command == "identity":
            version, roothash = running_bios_identity()
            print(version)
            print(roothash)
        elif args.command == "status":
            print(json.dumps(read_state(args.boot_dir), sort_keys=True))
        elif args.command == "stage":
            stage_trial(
                args.boot_dir,
                current_version=args.current_version,
                current_roothash=args.current_roothash,
                target_version=args.target_version,
                target_roothash=args.target_roothash,
            )
        elif args.command == "bless":
            bless_trial(args.boot_dir, args.current_version, args.current_roothash)
        elif args.command == "cancel":
            cancel_trial(
                args.boot_dir,
                current_version=args.current_version,
                current_roothash=args.current_roothash,
                target_version=args.target_version,
                target_roothash=args.target_roothash,
            )
        else:
            complete = finish_fallback(
                args.boot_dir,
                current_version=args.current_version,
                current_roothash=args.current_roothash,
                target_version=args.target_version,
                target_roothash=args.target_roothash,
            )
            print("fallback" if complete else "pending")
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"veyra-grub-ota: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
