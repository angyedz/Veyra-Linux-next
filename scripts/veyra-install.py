#!/usr/bin/env python3
"""Install Veyra Next directly onto a selected disk from the Live session."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import re
import shlex
import shutil
import struct
import stat
import subprocess
import sys
import tempfile


DEFAULT_SOURCE = pathlib.Path("/usr/share/veyra/installer/source")
SCRATCH_MOUNT = pathlib.Path("/run/veyra-installer/scratch")
FEDORA_RELEASE = "44"
ACCOUNT_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,30}")


def requested_account() -> tuple[str, str, str]:
    """Collect credentials before erasing any disk. Never log secrets."""
    username = input("New Veyra username: ").strip()
    if not ACCOUNT_NAME.fullmatch(username) or username in {"root", "liveuser", "nobody"}:
        raise InstallError("username must be a simple lowercase account name (not root/liveuser)")
    user_password = getpass.getpass("New user password: ")
    user_confirm = getpass.getpass("Repeat user password: ")
    root_password = getpass.getpass("Choose installed-system root password: ")
    root_confirm = getpass.getpass("Repeat installed root password: ")
    if user_password != user_confirm or root_password != root_confirm:
        raise InstallError("account passwords do not match; no disk was modified")
    if len(user_password) < 8 or len(root_password) < 8:
        raise InstallError("both passwords must contain at least eight characters")
    if any("\n" in value or "\x00" in value for value in (user_password, root_password)):
        raise InstallError("password contains invalid control characters")
    return username, user_password, root_password


def configure_installed_accounts(root: pathlib.Path, username: str, user_password: str, root_password: str) -> None:
    """Create installed accounts in its own root; never import Live credentials."""
    if not ACCOUNT_NAME.fullmatch(username):
        raise InstallError("invalid installed account name")
    command(["chroot", str(root), "/usr/sbin/useradd",
             "--create-home", "--groups", "wheel", "--shell", "/bin/bash", username])
    # Password input uses stdin, not command arguments or installer logs.
    accounts = f"{username}:{user_password}\nroot:{root_password}\n"
    subprocess.run(
        ["chroot", str(root), "/usr/sbin/chpasswd"],
        input=accounts, encoding="utf-8", check=True,
        stdout=subprocess.DEVNULL,
    )


def populate_installed_home(device: pathlib.Path, username: str, uid: int, gid: int) -> None:
    """Create an owned home directory on the persistent ext4 home partition."""
    if not ACCOUNT_NAME.fullmatch(username) or uid < 1000 or gid < 1000:
        raise InstallError("invalid persistent home owner")
    mountpoint = pathlib.Path("/run/veyra-installer/home")
    mountpoint.mkdir(parents=True, exist_ok=True)
    command(["mount", "-o", "nodev,nosuid", str(device), str(mountpoint)])
    try:
        destination = mountpoint / username
        if destination.exists() or destination.is_symlink():
            raise InstallError("refusing to overwrite a preexisting installed home")
        destination.mkdir(mode=0o700)
        os.chown(destination, uid, gid)
        command(["sync"])
    finally:
        command(["umount", str(mountpoint)])


class InstallError(RuntimeError):
    pass



def validate_recovery_uki_metadata(recovery: pathlib.Path) -> None:
    """Fail closed on malformed or wrong-identity Recovery UKIs.

    This is structural validation, NOT a signature check or a boot test.
    The prototype Recovery UKI is unsigned until a production trust root exists.
    """
    if recovery.is_symlink() or not recovery.is_file():
        raise InstallError("the Live ISO does not contain a regular Veyra Recovery UKI")
    size = recovery.stat().st_size
    if not 512 <= size <= 1024**3:
        raise InstallError("Recovery UKI has an implausible file size")
    try:
        with recovery.open("rb") as stream:
            dos = stream.read(64)
            if len(dos) != 64 or dos[:2] != b"MZ":
                raise ValueError("missing MZ executable header")
            pe_offset = struct.unpack_from("<I", dos, 0x3c)[0]
            if not 64 <= pe_offset <= size - 24:
                raise ValueError("invalid PE header offset")
            stream.seek(pe_offset)
            coff = stream.read(24)
            if len(coff) != 24 or coff[:4] != b"PE\x00\x00":
                raise ValueError("missing PE signature")
            count = struct.unpack_from("<H", coff, 6)[0]
            optional_size = struct.unpack_from("<H", coff, 20)[0]
            table = pe_offset + 24 + optional_size
            if not 4 <= count <= 32 or table + count * 40 > size:
                raise ValueError("invalid PE section table")
            sections: dict[bytes, tuple[int, int]] = {}
            for index in range(count):
                stream.seek(table + index * 40)
                header = stream.read(40)
                if len(header) != 40:
                    raise ValueError("truncated PE section header")
                name = header[:8].split(b"\x00", 1)[0]
                raw_size, raw_offset = struct.unpack_from("<II", header, 16)
                if name in sections:
                    raise ValueError("duplicate PE section")
                sections[name] = (raw_offset, raw_size)

            def read_section(name: bytes, maximum: int) -> bytes:
                if name not in sections:
                    raise ValueError(f"missing {name.decode('ascii')} UKI section")
                offset, length = sections[name]
                if not 0 < length <= maximum or offset < table + count * 40 or offset > size - length:
                    raise ValueError(f"invalid {name.decode('ascii')} UKI section bounds")
                stream.seek(offset)
                return stream.read(length)

            osrel = read_section(b".osrel", 16384).split(b"\x00", 1)[0].decode("utf-8")
            cmdline = read_section(b".cmdline", 4096).split(b"\x00", 1)[0]
            # The large kernel/initrd payloads need validated bounds, not
            # loading their bytes into the installer process.
            for name in (b".linux", b".initrd"):
                if name not in sections:
                    raise ValueError(f"missing {name.decode('ascii')} UKI section")
                offset, length = sections[name]
                if length == 0 or offset < table + count * 40 or offset > size - length:
                    raise ValueError(f"invalid {name.decode('ascii')} UKI payload bounds")
        fields: dict[str, str] = {}
        for line in osrel.splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in fields:
                raise ValueError(f"duplicate {key} .osrel field")
            parsed = shlex.split(value, comments=True, posix=True)
            if len(parsed) != 1:
                raise ValueError(f"invalid {key} .osrel field")
            fields[key] = parsed[0]
        expected = {"ID": "fedora", "VERSION_ID": FEDORA_RELEASE, "IMAGE_ID": "veyra-recovery"}
        for key, value in expected.items():
            if fields.get(key) != value:
                raise ValueError(f"{key} must be {value}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", fields.get("IMAGE_VERSION", "")):
            raise ValueError("missing or invalid IMAGE_VERSION")
        if b"rdinit=/init" not in cmdline.split():
            raise ValueError("Recovery UKI lacks its independent rdinit=/init")
    except (OSError, ValueError, UnicodeError, struct.error) as error:
        raise InstallError(f"invalid Veyra Recovery UKI metadata: {error}") from error



def command(args: list[str], *, capture: bool = False) -> str:
    print("+ " + " ".join(args), flush=True)
    result = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def parse_mkosi_config(path: pathlib.Path) -> tuple[str, list[str]]:
    section = ""
    version = ""
    packages: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "Output" and line.startswith("ImageVersion="):
            version = line.partition("=")[2].strip()
        elif section == "Content" and line.startswith("Packages="):
            name = line.partition("=")[2].strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.:-]*", name):
                raise InstallError(f"unsafe package name in mkosi.conf: {name!r}")
            packages.append(name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise InstallError("mkosi.conf has no valid ImageVersion in [Output]")
    if not packages or len(packages) != len(set(packages)):
        raise InstallError("mkosi.conf must contain a non-empty, duplicate-free package list")
    return version, packages


def parse_size(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)([KMGTP]?)", value.strip(), re.IGNORECASE)
    if not match:
        raise InstallError(f"unsupported partition size: {value!r}")
    multiplier = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
    }[match.group(2).upper()]
    return int(match.group(1)) * multiplier


def minimum_disk_size(definitions: pathlib.Path) -> int:
    total = 0
    for path in sorted(definitions.glob("*.conf")):
        section = ""
        sizes: list[int] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif section == "Partition" and line.startswith("SizeMinBytes="):
                sizes.append(parse_size(line.partition("=")[2]))
        if len(sizes) != 1:
            raise InstallError(f"{path.name} must define exactly one SizeMinBytes")
        total += sizes[0]
    if not total:
        raise InstallError(f"no partition definitions found in {definitions}")
    return total + 64 * 1024**2


def descendants(device: dict) -> list[dict]:
    result = [device]
    for child in device.get("children") or []:
        result.extend(descendants(child))
    return result


def live_media_parent(source: pathlib.Path | None, devices: list[dict]) -> str | None:
    if source is None:
        return None
    source_name = os.path.realpath(str(source))
    for device in devices:
        for child in descendants(device):
            if os.path.realpath(str(child.get("path") or "")) == source_name or child.get("name") == source.name:
                return device.get("path")
    return source_name if source_name.startswith("/dev/") else None


def list_installable_disks() -> tuple[list[dict], list[tuple[str, str]]]:
    raw = command(
        ["lsblk", "--json", "--bytes", "--paths", "--output", "PATH,NAME,TYPE,SIZE,MODEL,RM,RO,PKNAME,MOUNTPOINTS,MAJ:MIN,SERIAL,WWN"],
        capture=True,
    )
    devices = json.loads(raw).get("blockdevices", [])
    media_source = None
    source_result = subprocess.run(
        ["findmnt", "--noheadings", "--output", "SOURCE", "--target", "/run/initramfs/live"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )
    if source_result.returncode == 0 and source_result.stdout.strip():
        media_source = pathlib.Path(source_result.stdout.strip().split("[", 1)[0])
    live_disk = live_media_parent(media_source, devices)
    swaps = set()
    swap_result = subprocess.run(
        ["swapon", "--noheadings", "--raw", "--show", "--output", "NAME"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )
    if swap_result.returncode == 0:
        swaps = {line.strip() for line in swap_result.stdout.splitlines() if line.strip()}

    choices: list[dict] = []
    excluded: list[tuple[str, str]] = []
    for device in devices:
        path = str(device.get("path", ""))
        if device.get("type") != "disk":
            continue
        if not path.startswith("/dev/"):
            excluded.append((path or device.get("name", "unknown"), "not a device path"))
        elif path == live_disk:
            excluded.append((path, "contains the running Live media"))
        elif device.get("ro"):
            excluded.append((path, "read-only"))
        else:
            children = descendants(device)
            mounted = next(
                (
                    str(item.get("path") or item.get("name"))
                    for item in children
                    if any(point for point in (item.get("mountpoints") or []))
                ),
                None,
            )
            swapped = next(
                (
                    str(item.get("path") or item.get("name"))
                    for item in children
                    if str(item.get("path") or item.get("name")) in swaps
                ),
                None,
            )
            if mounted:
                excluded.append((path, f"has mounted filesystem {mounted}"))
            elif swapped:
                excluded.append((path, f"contains active swap {swapped}"))
            else:
                choices.append(device)
    return choices, excluded


def target_partitions(disk: pathlib.Path) -> list[dict]:
    raw = command(
        ["lsblk", "--json", "--paths", "--output", "PATH,TYPE,PARTLABEL,PARTTYPE"],
        capture=True,
    )
    devices = json.loads(raw).get("blockdevices", [])
    target = disk.resolve()
    parent = next(
        (item for item in devices if pathlib.Path(str(item.get("path", ""))).resolve() == target),
        None,
    )
    if parent is None:
        raise InstallError(f"target disk disappeared: {disk}")
    return [item for item in descendants(parent) if item.get("type") == "part"]


def find_partition(label: str, disk: pathlib.Path) -> pathlib.Path:
    matches = [
        pathlib.Path(str(item["path"]))
        for item in target_partitions(disk)
        if item.get("partlabel") == label
    ]
    if len(matches) != 1 or not matches[0].exists():
        raise InstallError(f"expected one {label!r} partition on {disk}, found {len(matches)}")
    return matches[0]


def find_partition_by_type(partition_type: str, disk: pathlib.Path) -> pathlib.Path:
    matches = [
        pathlib.Path(item["path"])
        for item in target_partitions(disk)
        if str(item.get("parttype", "")).lower() == partition_type.lower()
    ]
    if len(matches) != 1 or not matches[0].exists():
        raise InstallError(f"expected exactly one partition of type {partition_type}, found {len(matches)}")
    return matches[0]


def partition_uuid(device: pathlib.Path) -> str:
    value = command(["blkid", "--match-tag", "PARTUUID", "--output", "value", str(device)], capture=True)
    value = value.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise InstallError(f"invalid GPT partition UUID for {device}: {value!r}")
    return value


def overlay_veyra_files(root: pathlib.Path, source: pathlib.Path) -> None:
    extra = source / "mkosi.extra"
    command(["cp", "--archive", str(extra) + "/.", str(root)])


def seed_fedora_repositories(root: pathlib.Path) -> None:
    """Make the Live Fedora 44 repository definitions and signing keys available in the installroot."""
    for source, destination in (
        (pathlib.Path("/etc/yum.repos.d"), root / "etc/yum.repos.d"),
        (pathlib.Path("/etc/pki/rpm-gpg"), root / "etc/pki/rpm-gpg"),
    ):
        if not source.is_dir():
            raise InstallError(f"Fedora 44 repository data is missing from the Live session: {source}")
        destination.mkdir(parents=True, exist_ok=True)
        command(["cp", "--archive", str(source) + "/.", str(destination)])


def install_initrd_module(root: pathlib.Path, source: pathlib.Path) -> None:
    module_source = source / "installer/dracut/99veyra-sysroot"
    module_target = root / "usr/lib/dracut/modules.d/99veyra-sysroot"
    module_target.mkdir(parents=True, exist_ok=True)
    command(["cp", "--archive", str(module_source) + "/.", str(module_target)])


def write_recovery_uki(esp: pathlib.Path, source: pathlib.Path) -> None:
    recovery = source / "assets/veyra-recovery.efi"
    validate_recovery_uki_metadata(recovery)
    destination = esp / "EFI/Linux/veyra-recovery.efi"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recovery, destination)


def with_chroot_mounts(root: pathlib.Path) -> list[pathlib.Path]:
    mounted: list[pathlib.Path] = []
    try:
        for name in ("dev", "proc", "sys", "run"):
            destination = root / name
            destination.mkdir(parents=True, exist_ok=True)
            command(["mount", "--rbind", f"/{name}", str(destination)])
            mounted.append(destination)
            command(["mount", "--make-rslave", str(destination)])
    except BaseException:
        cleanup_mounts(mounted)
        raise
    return mounted


def cleanup_mounts(mounts: list[pathlib.Path]) -> None:
    for path in reversed(mounts):
        subprocess.run(["umount", "--recursive", str(path)], check=False)


def package_manifest(source: pathlib.Path) -> tuple[str, list[str]]:
    version, packages = parse_mkosi_config(source / "mkosi.conf")
    if any(name in {"dnf", "dnf5", "dnf5-plugins"} for name in packages):
        raise InstallError("the target package manifest must not install the DNF command-line frontend")
    if not {"libdnf5", "python3-libdnf5"}.issubset(packages):
        raise InstallError("the target package manifest must retain libdnf5 for vpkg")
    return version, packages



def validate_installer_assets(source: pathlib.Path) -> None:
    """Fail before any disk modifications when the Live ISO is incomplete."""
    required = (
        "mkosi.conf",
        "mkosi.extra/usr/lib/veyra/grub_ota.py",
        "installer/dracut/99veyra-sysroot/module-setup.sh",
        "assets/veyra-recovery.efi",
    )
    for relative in required:
        path = source / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise InstallError(f"Live installer missing or invalid required asset: {relative}")
    validate_recovery_uki_metadata(source / "assets/veyra-recovery.efi")
    definitions = source / "mkosi.repart"
    if not definitions.is_dir() or not list(definitions.glob("*.conf")):
        raise InstallError("Live installer has no mkosi.repart partition definitions")


def assert_target_identity_unchanged(original: dict, refreshed: dict) -> None:
    """Prevent simple removable-device and mount-state races before repartition."""
    for key in ("path", "size", "model", "maj:min", "serial", "wwn"):
        if str(original.get(key) or "").strip() != str(refreshed.get(key) or "").strip():
            raise InstallError(f"target disk identity changed during confirmation ({key})")


def choose_device(choices: list[dict], excluded: list[tuple[str, str]]) -> pathlib.Path:
    if not choices:
        raise InstallError("no unused writable disks are available")
    print("\nDisks available for installation:")
    for disk in choices:
        model = (disk.get("model") or "Unknown model").strip()
        size_gib = int(disk.get("size") or 0) / 1024**3
        print(f"  {disk['path']}  {model}  {size_gib:.1f} GiB")
    if excluded:
        print("\nDisks excluded for safety:")
        for path, reason in excluded:
            print(f"  {path}: {reason}")
    valid = {str(disk["path"]) for disk in choices}
    while True:
        candidate = input("\nDisk to erase and install Veyra onto: ").strip()
        if candidate in valid:
            return pathlib.Path(candidate)
        print("Enter one of the exact disk paths shown above.", file=sys.stderr)


def validate_target(target: pathlib.Path, source: pathlib.Path) -> tuple[dict, int]:
    target = target.resolve(strict=True)
    if not stat.S_ISBLK(target.stat().st_mode):
        raise InstallError(f"installation target is not a block device: {target}")
    choices, excluded = list_installable_disks()
    candidate = next((item for item in choices if pathlib.Path(item["path"]).resolve() == target), None)
    if candidate is None:
        reason = next((message for path, message in excluded if pathlib.Path(path).resolve() == target), None)
        raise InstallError(f"refusing installation target {target}: {reason or 'not an unused whole disk'}")
    size = int(candidate.get("size") or 0)
    required = minimum_disk_size(source / "mkosi.repart")
    if size < required:
        raise InstallError(
            f"{target} has {size / 1024**3:.1f} GiB; this prototype layout needs at least "
            f"{required / 1024**3:.1f} GiB"
        )
    return candidate, required


def write_image_identity(root: pathlib.Path, version: str) -> None:
    for relative in ("etc/os-release", "usr/lib/os-release", "usr/lib/veyra/os-release"):
        path = root / relative
        if not path.is_file():
            raise InstallError(f"expected OS release file is missing: {path}")
        fields = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                fields[key] = value
        if relative == "usr/lib/os-release" and fields.get("ID") != "fedora":
            raise InstallError("the immutable base must retain Fedora's vendor os-release identity")
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(("IMAGE_ID=", "IMAGE_VERSION="))]
        lines.extend((f'IMAGE_ID="veyra-next"', f'IMAGE_VERSION="{version}"'))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_system(target: pathlib.Path, source: pathlib.Path, confirmation: str | None = None) -> None:
    if os.geteuid() != 0:
        raise InstallError("run the installer with administrator privileges")
    source = source.resolve(strict=True)
    validate_installer_assets(source)
    version, packages = package_manifest(source)
    candidate, required = validate_target(target, source)
    disk = pathlib.Path(candidate["path"]).resolve(strict=True)
    model = (candidate.get("model") or "Unknown model").strip()
    size_gib = int(candidate.get("size") or 0) / 1024**3
    print(
        f"\nThis erases every partition on {disk} ({model}, {size_gib:.1f} GiB) and installs "
        f"Veyra Next Prototype {version}.\n"
        f"The current partition layout requires {required / 1024**3:.1f} GiB.\n"
        "The installer will download Fedora 44 packages, build the Veyra system on this disk, "
        "and install UEFI and Legacy BIOS boot files."
    )
    if confirmation is None:
        confirmation = input(f"Type ERASE {disk} to continue: ").strip()
    if confirmation != f"ERASE {disk}":
        raise InstallError("confirmation did not match; no disk changes were made")
    # Mounts or physical devices can change while the user reads the warning.
    # Never use the stale eligibility/identity decision for destructive repart.
    refreshed, required_again = validate_target(disk, source)
    assert_target_identity_unchanged(candidate, refreshed)
    if required_again != required:
        raise InstallError("required partition size changed during confirmation")
    # For safety require interactive credentials even with --confirm-device.
    # Do not begin destructive operations when no terminal can provide them.
    username, user_password, root_password = requested_account()

    scratch_conf_dir = pathlib.Path(tempfile.mkdtemp(prefix="veyra-scratch-repart-"))
    final_conf_dir = pathlib.Path(tempfile.mkdtemp(prefix="veyra-final-repart-"))
    scratch_conf = source / "mkosi.repart/50-home.conf"
    scratch_text = scratch_conf.read_text(encoding="utf-8")
    scratch_text = re.sub(r"(?m)^Format=.*$", "Format=btrfs", scratch_text)
    scratch_text = re.sub(r"(?m)^CopyFiles=.*\n?", "", scratch_text)
    (scratch_conf_dir / "50-home.conf").write_text(scratch_text, encoding="utf-8")
    for definition in (source / "mkosi.repart").glob("*.conf"):
        shutil.copy2(definition, final_conf_dir / definition.name)
    final_home = final_conf_dir / "50-home.conf"
    final_home.write_text(
        "\n".join(
            line for line in final_home.read_text(encoding="utf-8").splitlines()
            if not line.startswith(("Format=", "CopyFiles="))
        ) + "\n",
        encoding="utf-8",
    )
    root = pathlib.Path()
    scratch_mounted = False
    bind_mounts: list[pathlib.Path] = []
    esp_mounted = False
    try:
        command([
            "systemd-repart", "--dry-run=no", "--empty=force", "--seed=random",
            f"--definitions={scratch_conf_dir}", str(disk),
        ])
        command(["udevadm", "settle", "--timeout=30"])
        scratch_device = find_partition("VEYRA_HOME", disk)
        command(["mkfs.btrfs", "--force", "--label", "VEYRA_HOME", str(scratch_device)])
        SCRATCH_MOUNT.mkdir(parents=True, exist_ok=True)
        command(["mount", "-o", "compress=zstd", str(scratch_device), str(SCRATCH_MOUNT)])
        scratch_mounted = True
        root = SCRATCH_MOUNT / ".veyra-install/root"
        root.mkdir(parents=True)
        bind_mounts = with_chroot_mounts(root)

        print("\nInstalling Fedora 44 packages from the official repositories…", flush=True)
        seed_fedora_repositories(root)
        command([
            "dnf5", "--assumeyes", f"--installroot={root}",
            f"--releasever={FEDORA_RELEASE}", "install", *packages,
        ])
        overlay_veyra_files(root, source)
        # Accounts are created in the installed root, NOT cloned from Live.
        configure_installed_accounts(root, username, user_password, root_password)
        user_password = root_password = ""
        install_initrd_module(root, source)
        write_image_identity(root, version)

        # Save numeric ownership before the temporary root is unmounted.
        account_home = root / "home" / username
        if account_home.is_symlink() or not account_home.is_dir():
            raise InstallError("new user home missing from the staged root")
        home_uid, home_gid = account_home.stat().st_uid, account_home.stat().st_gid
        command(["systemd-machine-id-setup", f"--root={root}"])
        command(["systemctl", f"--root={root}", "preset-all", "--preset-mode=enable-only"])
        flatpak_repo = "/usr/share/veyra/flathub.flatpakrepo"
        command(["chroot", str(root), "/usr/bin/flatpak", "remote-add", "--system", "--if-not-exists", "flathub", flatpak_repo])

        kernel_candidates = sorted((root / "usr/lib/modules").glob("*/vmlinuz"))
        if len(kernel_candidates) != 1 or not kernel_candidates[0].is_file():
            raise InstallError(f"expected one installed Fedora kernel payload, found {kernel_candidates!r}")
        linux = kernel_candidates[0]
        kernel_version = linux.parent.name
        initrd = root / f"boot/initramfs-{kernel_version}.img"
        command([
            "chroot", str(root), "/usr/bin/dracut", "--force", "--no-hostonly",
            "--kver", kernel_version, f"/boot/initramfs-{kernel_version}.img",
        ])
        if not initrd.is_file() or initrd.stat().st_size == 0:
            raise InstallError("dracut did not produce the Veyra initramfs")

        cleanup_mounts(bind_mounts)
        bind_mounts = []

        for cache in (root / "var/cache/dnf", root / "var/cache/libdnf5"):
            if cache.exists():
                shutil.rmtree(cache)
        command([
            "systemd-repart", "--dry-run=no", "--empty=allow", "--seed=random",
            f"--definitions={final_conf_dir}", f"--root={root}", str(disk),
        ])
        command(["udevadm", "settle", "--timeout=30"])

        home_device = find_partition("VEYRA_HOME", disk)
        command(["umount", str(SCRATCH_MOUNT)])
        scratch_mounted = False
        command(["mkfs.ext4", "--force", "-F", "-L", "VEYRA_HOME", str(home_device)])
        populate_installed_home(home_device, username, home_uid, home_gid)

        root_label = f"veyra-next_{version}"
        verity_label = f"{root_label}_verity"
        root_device = find_partition(root_label, disk)
        verity_device = find_partition(verity_label, disk)
        roothash = partition_uuid(root_device) + partition_uuid(verity_device)
        if not re.fullmatch(r"[0-9a-f]{64}", roothash):
            raise InstallError("systemd-repart produced an invalid root/verity identity")
        command(["veritysetup", "verify", str(root_device), str(verity_device), roothash])

        esp_device = find_partition_by_type("c12a7328-f81f-11d2-ba4b-00a0c93ec93b", disk)
        esp = pathlib.Path("/run/veyra-installer/esp")
        esp.mkdir(parents=True, exist_ok=True)
        command(["mount", str(esp_device), str(esp)])
        esp_mounted = True
        command(["bootctl", "--no-variables", f"--esp-path={esp}", "install"])

        uki = esp / f"EFI/Linux/veyra-next_{version}+3-0.efi"
        uki.parent.mkdir(parents=True, exist_ok=True)
        command([
            "ukify", "build", f"--linux={linux}", f"--initrd={initrd}",
            f"--cmdline=roothash={roothash} systemd.firstboot=no systemd.unit=graphical.target module.sig_enforce=1",
            f"--os-release=@{root / 'usr/lib/os-release'}", f"--uname={kernel_version}",
            f"--output={uki}",
        ])
        loader_conf = esp / "loader/loader.conf"
        loader_conf.parent.mkdir(parents=True, exist_ok=True)
        loader_conf.write_text(f"default veyra-next_{version}+3-0.efi\ntimeout 5\n", encoding="ascii")
        write_recovery_uki(esp, source)

        bios_dir = esp / "VEYRA"
        bios_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(linux, bios_dir / f"veyra-next_{version}.vmlinuz")
        shutil.copy2(initrd, bios_dir / f"veyra-next_{version}.initrd")
        grub_dir = esp / "grub2"
        grub_dir.mkdir(parents=True, exist_ok=True)
        helper_path = source / "mkosi.extra/usr/lib/veyra/grub_ota.py"
        sys.path.insert(0, str(helper_path.parent))
        import grub_ota  # noqa: PLC0415

        (grub_dir / "grub.cfg").write_text(grub_ota.render_grub_config(version, roothash), encoding="ascii")
        grub_ota.initialize_grubenv(grub_dir / "grubenv", version, roothash)
        command(["grub2-install", "--target=i386-pc", f"--boot-directory={esp}", "--recheck", str(disk)])

        command(["sync"])
        print(
            f"\nVeyra Next Prototype {version} is installed on {disk}. "
            "Remove the Live USB and reboot to start the installed system."
        )
    finally:
        cleanup_mounts(bind_mounts)
        if esp_mounted:
            subprocess.run(["umount", str(pathlib.Path("/run/veyra-installer/esp"))], check=False)
        if scratch_mounted:
            subprocess.run(["umount", str(SCRATCH_MOUNT)], check=False)
        shutil.rmtree(scratch_conf_dir, ignore_errors=True)
        shutil.rmtree(final_conf_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=pathlib.Path)
    parser.add_argument("--confirm-device", help=argparse.SUPPRESS)
    parser.add_argument("--list-disks", action="store_true", help="show eligible target disks")
    parser.add_argument("--preflight", action="store_true", help="validate installation assets and space without changing disks")
    args = parser.parse_args()

    try:
        source = args.source_root.resolve(strict=True)
        if args.preflight:
            validate_installer_assets(source)
            version, packages = package_manifest(source)
            minimum = minimum_disk_size(source / "mkosi.repart")
            if args.target is not None:
                validate_target(args.target, source)
            print(f"Veyra Next {version}: {len(packages)} packages; minimum target capacity {minimum / 1024**3:.1f} GiB")
            print("Preflight only: no disk changes were made.")
            return 0
        if args.list_disks:
            choices, excluded = list_installable_disks()
            for disk in choices:
                size_gib = int(disk.get("size") or 0) / 1024**3
                print(f"{disk['path']}\t{(disk.get('model') or 'Unknown model').strip()}\t{size_gib:.1f} GiB")
            for path, reason in excluded:
                print(f"EXCLUDED\t{path}\t{reason}")
            return 0
        target = args.target or choose_device(*list_installable_disks())
        if args.confirm_device is not None:
            # Non-interactive automation can proceed only when it names the exact target twice.
            if args.target is None or args.confirm_device != str(args.target):
                raise InstallError("automated confirmation must exactly match --target")
            if os.geteuid() != 0:
                raise InstallError("run the installer with administrator privileges")
            install_system(target, source, f"ERASE {args.target.resolve(strict=True)}")
        else:
            install_system(target, source)
    except (InstallError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"Veyra installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
