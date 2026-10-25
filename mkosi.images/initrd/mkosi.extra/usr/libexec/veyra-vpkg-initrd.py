#!/usr/bin/python3
"""Select and mount the ext4-backed vpkg generation before initrd switch-root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid


ROOTHASH_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
GENERATION_RE = re.compile(r"^generation-[0-9a-f]{32}$")
STATE_RELATIVE = Path("var/lib/veyra/vpkg/system-generations")


class InitrdError(RuntimeError):
    pass


def parse_os_release(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as error:
        raise InitrdError("The selected UKI has a malformed .osrel section.") from error
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key not in {"ID", "VERSION_ID", "IMAGE_VERSION"}:
            continue
        if key in values:
            raise InitrdError(f"The selected UKI .osrel repeats {key}.")
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if not value or any(c in value for c in "\x00\n\r"):
            raise InitrdError(f"The selected UKI .osrel has an invalid {key}.")
        values[key] = value
    if set(values) != {"ID", "VERSION_ID", "IMAGE_VERSION"}:
        raise InitrdError("The selected UKI .osrel must define ID, VERSION_ID, and IMAGE_VERSION.")
    if values["ID"] != "fedora" or values["VERSION_ID"] != "44":
        raise InitrdError("The vpkg initrd generation backend currently supports Fedora 44 only.")
    if not VERSION_RE.fullmatch(values["IMAGE_VERSION"]):
        raise InitrdError("The selected UKI .osrel has an unsafe IMAGE_VERSION.")
    return values


def read_uki_image_version(uki: Path) -> str:
    try:
        size = uki.stat().st_size
        with uki.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                raise ValueError("missing DOS header")
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            if pe_offset + 24 > size:
                raise ValueError("truncated PE header")
            stream.seek(pe_offset)
            coff = stream.read(24)
            if len(coff) != 24 or coff[:4] != b"PE\0\0":
                raise ValueError("missing PE header")
            section_count = struct.unpack_from("<H", coff, 6)[0]
            optional_size = struct.unpack_from("<H", coff, 20)[0]
            table_offset = pe_offset + 24 + optional_size
            if not 0 < section_count <= 96 or optional_size > 4096:
                raise ValueError("invalid PE dimensions")
            if table_offset + section_count * 40 > size:
                raise ValueError("truncated PE section table")
            osrel_section: tuple[int, int] | None = None
            stream.seek(table_offset)
            for _ in range(section_count):
                section = stream.read(40)
                if len(section) != 40:
                    raise ValueError("truncated PE section table")
                name = section[:8].split(b"\0", 1)[0]
                if name == b".osrel":
                    raw_size, raw_offset = struct.unpack_from("<II", section, 16)
                    if not raw_size or raw_size > 1024 * 1024 or raw_offset + raw_size > size:
                        raise ValueError("invalid .osrel section size")
                    osrel_section = (raw_offset, raw_size)
            if osrel_section is None:
                raise ValueError("missing .osrel section")
            stream.seek(osrel_section[0])
            osrel = parse_os_release(stream.read(osrel_section[1]).split(b"\0", 1)[0])
            return osrel["IMAGE_VERSION"]
    except (OSError, ValueError, struct.error) as error:
        raise InitrdError(f"Cannot read the selected UKI identity from {uki}: {error}") from error


def deployment_identity(command_line: str, uki: Path) -> tuple[str, str]:
    hashes = [token.partition("=")[2] for token in command_line.split() if token.startswith("roothash=")]
    if len(hashes) != 1 or not ROOTHASH_RE.fullmatch(hashes[0]):
        raise InitrdError("The running UKI must provide exactly one valid roothash.")
    return hashes[0], read_uki_image_version(uki)


def safe_generation_id(value: str) -> str:
    if not GENERATION_RE.fullmatch(value):
        raise InitrdError("The active vpkg system-generation pointer is malformed.")
    return value


def ensure_real_directory(path: Path, mode: int = 0o700) -> None:
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            cursor.mkdir(mode=mode)
            info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise InitrdError(f"Refusing a symlink in vpkg generation state: {cursor}")
        if not stat.S_ISDIR(info.st_mode):
            raise InitrdError(f"Vpkg generation state is not a directory: {cursor}")
    info = absolute.lstat()
    if os.geteuid() == 0 and info.st_uid != 0:
        raise InitrdError(f"Vpkg generation state is not root-owned: {absolute}")
    if info.st_mode & 0o022:
        raise InitrdError(f"Vpkg generation state is group- or world-writable: {absolute}")


def validate_tree_for_copy(source: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise InitrdError(f"The base RPM database is missing or is not a real directory: {source}")
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in directories + filenames:
            path = current_path / name
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise InitrdError(f"The base RPM database contains an unsupported file: {path}")


def require_real_directories(generation: Path, relatives: tuple[str, ...]) -> None:
    for relative in relatives:
        path = generation / relative
        try:
            info = path.lstat()
        except OSError as error:
            raise InitrdError(f"The vpkg system generation is missing {relative}.") from error
        if not stat.S_ISDIR(info.st_mode):
            raise InitrdError(f"The vpkg system generation has an unsafe {relative}.")


def read_active_generation(identity_dir: Path, roothash: str, image_version: str) -> Path | None:
    active = identity_dir / "active"
    if not active.is_symlink():
        if active.exists():
            raise InitrdError("The vpkg active system-generation selector is not a symlink.")
        return None
    generation_id = safe_generation_id(os.readlink(active))
    generation = identity_dir / generation_id
    if generation.is_symlink() or not generation.is_dir():
        raise InitrdError("The active vpkg system generation is unavailable.")
    try:
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InitrdError("The active vpkg system-generation manifest is unreadable.") from error
    expected = {"schema": 1, "generation_id": generation_id, "base_roothash": roothash,
                "uki_image_version": image_version}
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        raise InitrdError("The active vpkg system generation belongs to a different deployment identity.")
    require_real_directories(generation, (
        "upper", "upper/usr", "upper/etc", "upper/usr/lib",
        "upper/usr/lib/sysimage", "upper/usr/lib/sysimage/rpm",
        "work", "work/usr", "work/etc",
    ))
    rpmdb = generation / "upper/usr/lib/sysimage/rpm"
    if manifest.get("rpmdb_sha256") != rpmdb_digest(rpmdb):
        raise InitrdError("The active vpkg generation RPM database failed its integrity check.")
    return generation


def rpmdb_digest(rpmdb: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(rpmdb.rglob("*")):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise InitrdError(f"The generation RPM database contains an unsupported entry: {path}")
        digest.update(path.relative_to(rpmdb).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def fsync_tree(root: Path) -> None:
    directories: list[Path] = []
    files: list[Path] = []
    for current, names, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        for name in names:
            entry = current_path / name
            if not entry.is_symlink():
                directories.append(entry)
        for name in filenames:
            entry = current_path / name
            if not entry.is_symlink():
                files.append(entry)
    for path in files:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for path in reversed(directories):
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_generation(identity_dir: Path, generation_id: str, roothash: str, image_version: str) -> Path:
    generation_id = safe_generation_id(generation_id)
    generation = identity_dir / generation_id
    if generation.is_symlink() or not generation.is_dir():
        raise InitrdError("The requested vpkg system generation is unavailable.")
    try:
        manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InitrdError("The requested vpkg system-generation manifest is unreadable.") from error
    expected = {"schema": 1, "generation_id": generation_id, "base_roothash": roothash,
                "uki_image_version": image_version}
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        raise InitrdError("The requested vpkg system generation belongs to a different deployment identity.")
    require_real_directories(generation, (
        "upper", "upper/usr", "upper/etc", "upper/usr/lib",
        "upper/usr/lib/sysimage", "upper/usr/lib/sysimage/rpm",
        "work", "work/usr", "work/etc",
    ))
    rpmdb = generation / "upper/usr/lib/sysimage/rpm"
    if manifest.get("rpmdb_sha256") != rpmdb_digest(rpmdb):
        raise InitrdError("The requested vpkg system generation has an invalid RPM database.")
    return generation


def generation_from_cmdline(command_line: str) -> str | None:
    values = [token.partition("=")[2] for token in command_line.split()
              if token.startswith("veyra.vpkg-generation=")]
    if not values:
        return None
    if len(values) != 1:
        raise InitrdError("The UKI must provide at most one vpkg generation selector.")
    return safe_generation_id(values[0])


def set_active_generation(identity_dir: Path, generation_id: str) -> None:
    generation_id = safe_generation_id(generation_id)
    generation = identity_dir / generation_id
    if generation.is_symlink() or not generation.is_dir():
        raise InitrdError("Cannot select an unavailable vpkg system generation.")
    temporary = identity_dir / f".active-{uuid.uuid4().hex}"
    temporary.symlink_to(generation_id, target_is_directory=True)
    os.replace(temporary, identity_dir / "active")
    directory_fd = os.open(identity_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def create_trial_generation(sysroot: Path, roothash: str, image_version: str) -> Path:
    identity_dir = sysroot / STATE_RELATIVE / f"{roothash}--{image_version}"
    ensure_real_directory(identity_dir)
    parent = read_active_generation(identity_dir, roothash, image_version)
    if parent is None:
        raise InitrdError("A system-package candidate requires an initialized active generation.")
    generation_id = f"generation-{uuid.uuid4().hex}"
    generation = identity_dir / generation_id
    ensure_real_directory(generation)
    # RPM payloads legitimately contain links such as /usr/lib64 -> lib64.
    # Preserve those directory entries; dereferencing them could copy content
    # from outside the generation and silently change the package payload.
    shutil.copytree(parent / "upper", generation / "upper", symlinks=True)
    for relative in ("usr", "etc"):
        ensure_real_directory(generation / "work" / relative)
    rpmdb = generation / "upper/usr/lib/sysimage/rpm"
    if rpmdb.is_symlink() or not rpmdb.is_dir():
        shutil.rmtree(generation, ignore_errors=True)
        raise InitrdError("The active system generation has no generation-owned RPM database.")
    manifest = {
        "schema": 1,
        "generation_id": generation_id,
        "parent_generation": parent.name,
        "base_roothash": roothash,
        "uki_image_version": image_version,
        "rpmdb_path": "upper/usr/lib/sysimage/rpm",
        "upper_dirs": ["upper/usr", "upper/etc"],
        "work_dirs": ["work/usr", "work/etc"],
        "rpmdb_sha256": rpmdb_digest(rpmdb),
        "state": "trial",
    }
    manifest_path = generation / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    fsync_tree(generation)
    return generation


def select_generation_for_boot(sysroot: Path, roothash: str, image_version: str,
                               command_line: str) -> Path:
    identity_dir = sysroot / STATE_RELATIVE / f"{roothash}--{image_version}"
    requested_id = generation_from_cmdline(command_line)
    if requested_id is not None:
        return read_generation(identity_dir, requested_id, roothash, image_version)
    generation = ensure_base_generation(sysroot, roothash, image_version)
    active = read_active_generation(identity_dir, roothash, image_version)
    if active is None or active != generation:
        raise InitrdError("The active vpkg system generation changed while selecting the boot layer.")
    return generation


def complete_trial_generation(sysroot: Path, roothash: str, image_version: str,
                              generation_id: str) -> None:
    identity_dir = sysroot / STATE_RELATIVE / f"{roothash}--{image_version}"
    generation = read_generation(identity_dir, generation_id, roothash, image_version)
    manifest = json.loads((generation / "manifest.json").read_text(encoding="utf-8"))
    active = read_active_generation(identity_dir, roothash, image_version)
    parent_id = manifest.get("parent_generation")
    if not isinstance(parent_id, str) or active is None or active.name != parent_id:
        raise InitrdError("The trial generation parent is no longer the active generation.")
    manifest["state"] = "active"
    temporary = generation / ".manifest.json.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, generation / "manifest.json")
    fsync_tree(generation)
    set_active_generation(identity_dir, generation.name)


def rollback_generation(sysroot: Path, roothash: str, image_version: str) -> Path:
    identity_dir = sysroot / STATE_RELATIVE / f"{roothash}--{image_version}"
    active = read_active_generation(identity_dir, roothash, image_version)
    if active is None:
        raise InitrdError("There is no active vpkg system generation to roll back.")
    manifest = json.loads((active / "manifest.json").read_text(encoding="utf-8"))
    parent_id = manifest.get("parent_generation")
    if not isinstance(parent_id, str):
        raise InitrdError("The active vpkg system generation has no rollback parent.")
    parent = read_generation(identity_dir, parent_id, roothash, image_version)
    set_active_generation(identity_dir, parent.name)
    return parent


def ensure_base_generation(sysroot: Path, roothash: str, image_version: str) -> Path:
    if not ROOTHASH_RE.fullmatch(roothash) or not VERSION_RE.fullmatch(image_version):
        raise InitrdError("The deployment identity is malformed.")
    state_root = sysroot / STATE_RELATIVE
    ensure_real_directory(state_root)
    identity_dir = state_root / f"{roothash}--{image_version}"
    ensure_real_directory(identity_dir)
    active_generation = read_active_generation(identity_dir, roothash, image_version)
    if active_generation is not None:
        return active_generation

    base_rpmdb = sysroot / "usr/lib/sysimage/rpm"
    validate_tree_for_copy(base_rpmdb)
    generation_id = f"generation-{uuid.uuid4().hex}"
    generation = identity_dir / generation_id
    ensure_real_directory(generation)
    upper_usr = generation / "upper/usr"
    upper_etc = generation / "upper/etc"
    work_usr = generation / "work/usr"
    work_etc = generation / "work/etc"
    ensure_real_directory(upper_usr)
    ensure_real_directory(upper_etc)
    ensure_real_directory(work_usr)
    ensure_real_directory(work_etc)
    target_rpmdb = upper_usr / "lib/sysimage/rpm"
    ensure_real_directory(target_rpmdb.parent)
    shutil.copytree(base_rpmdb, target_rpmdb, symlinks=False)
    manifest = {
        "schema": 1,
        "generation_id": generation_id,
        "parent_generation": None,
        "base_roothash": roothash,
        "uki_image_version": image_version,
        "rpmdb_path": "upper/usr/lib/sysimage/rpm",
        "upper_dirs": ["upper/usr", "upper/etc"],
        "work_dirs": ["work/usr", "work/etc"],
        "rpmdb_sha256": rpmdb_digest(target_rpmdb),
    }
    manifest_tmp = generation / ".manifest.json.tmp"
    with manifest_tmp.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(manifest_tmp, generation / "manifest.json")
    fsync_tree(generation)
    set_active_generation(identity_dir, generation_id)
    return generation


def mount_generation(sysroot: Path, generation: Path) -> None:
    mounts = (
        ("usr", generation / "upper/usr", generation / "work/usr"),
        ("etc", generation / "upper/etc", generation / "work/etc"),
    )
    mounted: list[Path] = []
    try:
        for name, upper, work in mounts:
            lower = sysroot / name
            if lower.is_symlink() or not lower.is_dir():
                raise InitrdError(f"The selected base has no real /{name} directory.")
            for path in (upper, work):
                if path.is_symlink() or not path.is_dir():
                    raise InitrdError(f"The active vpkg generation has an unsafe /{name} layer.")
            if upper.stat().st_dev != work.stat().st_dev:
                raise InitrdError(f"The /{name} OverlayFS upper and work directories must share a filesystem.")
            subprocess.run(
                ["mount", "-t", "overlay", "overlay", "-o",
                 f"lowerdir={lower},upperdir={upper},workdir={work}", str(lower)],
                check=True,
            )
            mounted.append(lower)
    except (OSError, subprocess.CalledProcessError, InitrdError) as error:
        for mountpoint in reversed(mounted):
            subprocess.run(["umount", str(mountpoint)], check=False)
        if isinstance(error, InitrdError):
            raise
        raise InitrdError(f"Could not mount the selected vpkg system generation: {error}") from error


def selected_uki_path(
    sysroot: Path,
    *,
    efi_roots: tuple[Path, ...] = (
        Path("/sysefi"), Path("/efi"), Path("/boot"), Path("/boot/efi")
    ),
) -> Path:
    try:
        result = subprocess.run(
            ["bootctl", "--print-stub-path"],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InitrdError(f"Cannot determine which UKI is booting this deployment: {error}") from error
    raw = result.stdout.strip()
    reported = Path(raw)
    if not reported.is_absolute():
        raise InitrdError("bootctl returned a non-absolute path to the selected UKI.")

    candidates: list[tuple[Path, Path]] = []
    for esp_root in efi_roots:
        try:
            root = esp_root.resolve(strict=True)
        except OSError:
            continue
        if not root.is_dir():
            continue
        # systemd versions may report an ESP-root-relative path (/EFI/...) or
        # the mounted path (/efi/EFI/...).  Resolve both forms only beneath a
        # known mounted ESP root; never reinterpret an arbitrary absolute path.
        if reported.is_relative_to(esp_root) or reported.is_relative_to(root):
            candidates.append((reported, root))
        else:
            candidates.append((root / reported.relative_to("/"), root))

    for candidate, root in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_relative_to(root) and resolved.is_file():
                return resolved
        except OSError:
            continue
    raise InitrdError(
        "The selected UKI is not readable under a mounted EFI System Partition: "
        f"{reported} (sysroot {sysroot})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, default=Path("/sysroot"))
    parser.add_argument("--cmdline", type=Path, default=Path("/proc/cmdline"))
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise InitrdError("The vpkg initrd generation selector must run as root.")
        sysroot = args.sysroot.resolve(strict=True)
        command_line = args.cmdline.read_text(encoding="ascii")
        roothash, image_version = deployment_identity(command_line, selected_uki_path(sysroot))
        generation = select_generation_for_boot(sysroot, roothash, image_version, command_line)
        mount_generation(sysroot, generation)
        print(
            f"Veyra vpkg generation mounted: {roothash} {image_version} {generation.name}",
            flush=True,
        )
        return 0
    except (OSError, UnicodeError, InitrdError) as error:
        print(f"veyra-vpkg-initrd: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
