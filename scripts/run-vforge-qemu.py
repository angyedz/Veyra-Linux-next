#!/usr/bin/env python3
"""Prepare a disposable Fedora 44 Veyra disk and run Vforge inside QEMU/KVM."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_UID = 4242
TEST_GID = 4242
TEST_SUBID = 200_000
TEST_SUBID_COUNT = 65_536
PASS_MARKER = "VFORGE_QEMU_TEST_PASS"
FAIL_MARKER = "VFORGE_QEMU_TEST_FAIL"
REQUIRED_PARTITIONS = {"esp", "VEYRA_ETC", "VEYRA_VAR", "home"}


class VforgeQemuError(RuntimeError):
    pass


def run(command: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()
        raise VforgeQemuError(f"command failed ({result.returncode}): {' '.join(command)}" + (f"\n{detail}" if detail else ""))
    return result


def parse_os_release(contents: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in contents.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key] = value.strip().strip('"').strip("'")
    return fields


def require_fedora44_veyra(contents: str) -> dict[str, str]:
    fields = parse_os_release(contents)
    if fields.get("ID") != "fedora" or fields.get("VERSION_ID") != "44" or fields.get("IMAGE_ID") != "veyra-next":
        raise VforgeQemuError(f"expected the current Fedora 44 Veyra image; found {fields!r}")
    return fields


def require_size_cap_packages(contents: str) -> None:
    try:
        manifest = json.loads(contents)
        packages = manifest["packages"]
        if not isinstance(packages, list):
            raise TypeError("packages is not a list")
        names = {
            package["name"]
            for package in packages
            if isinstance(package, dict) and package.get("type") == "rpm" and isinstance(package.get("name"), str)
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise VforgeQemuError("Veyra image RPM manifest is malformed") from error
    missing = {"e2fsprogs"} - names
    if missing:
        raise VforgeQemuError(
            "Fedora 44 Veyra image is missing size-cap runtime packages: " + ", ".join(sorted(missing))
        )


def partition_offsets(contents: str) -> dict[str, tuple[int, int]]:
    try:
        table = json.loads(contents)["partitiontable"]
        sector_size = int(table["sectorsize"])
        partitions = table["partitions"]
        offsets = {
            str(partition["name"]): (
                int(partition["start"]) * sector_size,
                int(partition["size"]) * sector_size,
            )
            for partition in partitions
            if partition.get("name") is not None
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise VforgeQemuError("could not parse the Veyra GPT layout") from error
    missing = REQUIRED_PARTITIONS - offsets.keys()
    if missing:
        raise VforgeQemuError(f"Veyra test image is missing required partitions: {', '.join(sorted(missing))}")
    return offsets


def validate_base_image(image: pathlib.Path) -> tuple[dict[str, tuple[int, int]], str, str]:
    image = image.resolve(strict=True)
    if not image.is_file() or image.stat().st_size < 1024**3:
        raise VforgeQemuError(f"not a Veyra disk image: {image}")
    osrelease_path = image.with_name(f"{image.stem}.osrelease")
    manifest_path = image.with_name(f"{image.stem}.manifest")
    roothash_path = image.with_name("veyra-next.roothash")
    try:
        osrelease = require_fedora44_veyra(osrelease_path.read_text(encoding="utf-8"))
        require_size_cap_packages(manifest_path.read_text(encoding="utf-8"))
        roothash = roothash_path.read_text(encoding="ascii").strip().lower()
    except OSError as error:
        raise VforgeQemuError(f"image metadata sidecars (including RPM manifest) are missing next to {image}") from error
    if not re.fullmatch(r"[0-9a-f]{64}", roothash):
        raise VforgeQemuError("Veyra image has an invalid dm-verity root hash sidecar")
    run([sys.executable, str(ROOT / "scripts/check-image.py"), str(image)])
    layout = run(["sfdisk", "--json", str(image)], capture=True)
    offsets = partition_offsets(layout.stdout)
    return offsets, str(osrelease["IMAGE_VERSION"]), roothash


def ensure_regular_file(path: pathlib.Path, default_mode: int) -> tuple[str, int]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "", default_mode
    if not stat.S_ISREG(info.st_mode):
        raise VforgeQemuError(f"refusing to update non-regular guest account file: {path}")
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VforgeQemuError(f"could not read guest account file {path}: {error}") from error
    return contents, stat.S_IMODE(info.st_mode)


def append_account_record(
    path: pathlib.Path, name: str, record: str, *, default_mode: int, numeric_field: int | None = None,
    subid_range: tuple[int, int] | None = None,
) -> None:
    contents, mode = ensure_regular_file(path, default_mode)
    lines = [line for line in contents.splitlines() if line]
    for line in lines:
        fields = line.split(":")
        if fields[0] == name:
            if line == record:
                return
            raise VforgeQemuError(f"guest account entry {name!r} already exists with different values in {path.name}")
        if numeric_field is not None and len(fields) > numeric_field and fields[numeric_field].isdigit():
            if int(fields[numeric_field]) == TEST_UID:
                raise VforgeQemuError(f"guest already uses UID 4242 in {path.name}")
        if subid_range is not None and len(fields) == 3 and fields[1].isdigit() and fields[2].isdigit():
            start, count = int(fields[1]), int(fields[2])
            requested_start, requested_count = subid_range
            if start < requested_start + requested_count and requested_start < start + count:
                raise VforgeQemuError(f"guest sub-ID range overlaps an existing entry in {path.name}")

    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    updated = "\n".join([*lines, record]) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        os.write(descriptor, updated.encode("utf-8"))
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def inject_test_machine_id(etc_root: pathlib.Path) -> str:
    path = etc_root / "machine-id"
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode):
            raise VforgeQemuError("guest /etc/machine-id must be a regular file")

    machine_id = uuid.uuid4().hex
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o444)
    try:
        os.write(descriptor, (machine_id + "\n").encode("ascii"))
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return machine_id


def inject_test_user(etc_root: pathlib.Path, home_root: pathlib.Path) -> None:
    passwd = etc_root / "passwd"
    group = etc_root / "group"
    shadow = etc_root / "shadow"
    append_account_record(
        passwd, "vforge-test", "vforge-test:x:4242:4242:Vforge QEMU test:/home/vforge-test:/bin/bash",
        default_mode=0o644, numeric_field=2,
    )
    append_account_record(
        group, "vforge-test", "vforge-test:x:4242:", default_mode=0o644, numeric_field=2,
    )
    append_account_record(shadow, "vforge-test", "vforge-test:!:20000:0:99999:7:::", default_mode=0o000)
    append_account_record(
        etc_root / "subuid", "vforge-test", f"vforge-test:{TEST_SUBID}:{TEST_SUBID_COUNT}",
        default_mode=0o644, subid_range=(TEST_SUBID, TEST_SUBID_COUNT),
    )
    append_account_record(
        etc_root / "subgid", "vforge-test", f"vforge-test:{TEST_SUBID}:{TEST_SUBID_COUNT}",
        default_mode=0o644, subid_range=(TEST_SUBID, TEST_SUBID_COUNT),
    )

    user_home = home_root / "vforge-test"
    if user_home.is_symlink():
        raise VforgeQemuError("the Vforge QEMU test home may not be a symlink")
    user_home.mkdir(mode=0o700, exist_ok=True)
    os.chmod(user_home, 0o700)
    os.chown(user_home, TEST_UID, TEST_GID)


def test_runner_script() -> str:
    return f"""#!/bin/bash
set -u

source_root=/run/vforge-source
mkdir -p "$source_root"
if ! mount -t 9p -o trans=virtio,version=9p2000.L,ro vforge-source "$source_root"; then
    printf '%s\\n' '{FAIL_MARKER} mount' > /dev/console
    systemctl poweroff --force --force || true
    exit 1
fi

install -d -m 0700 -o {TEST_UID} -g {TEST_GID} /run/user/{TEST_UID}
network_ready=0
for ((attempt = 0; attempt < 120; attempt++)); do
    if getent hosts registry.fedoraproject.org >/dev/null 2>&1; then
        network_ready=1
        break
    fi
    sleep 1
done
if (( network_ready == 0 )); then
    printf '%s\\n' '{FAIL_MARKER} guest network unavailable' > /dev/console
    systemctl poweroff --force --force || true
    exit 1
fi
set +e
runuser --user vforge-test -- env \\
    HOME=/home/vforge-test \\
    XDG_RUNTIME_DIR=/run/user/{TEST_UID} \\
    XDG_DATA_HOME=/home/vforge-test/.local/share \\
    XDG_CONFIG_HOME=/home/vforge-test/.config \\
    XDG_STATE_HOME=/home/vforge-test/.local/state \\
    PATH="$source_root/mkosi.extra/usr/bin:/usr/bin:/bin" \\
    PYTHONDONTWRITEBYTECODE=1 \\
    /bin/bash "$source_root/scripts/test-vforge-qemu.sh"
status=$?
if (( status == 0 )); then
    printf '%s\\n' '{PASS_MARKER}' > /dev/console
else
    printf '%s %s\\n' '{FAIL_MARKER}' "$status" > /dev/console
fi
sleep 2
systemctl poweroff --force --force || true
exit "$status"
"""


def test_runner_unit() -> str:
    return """[Unit]
Description=Disposable Vforge QEMU integration scenario
Wants=network-online.target
After=network-online.target NetworkManager.service

[Service]
Type=oneshot
ExecStart=/bin/bash /etc/veyra-vforge-qemu-test-runner
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
"""


def write_guest_test_service(etc_root: pathlib.Path) -> None:
    systemd = etc_root / "systemd/system"
    wants = systemd / "multi-user.target.wants"
    wants.mkdir(mode=0o755, parents=True, exist_ok=True)
    unit = systemd / "vforge-qemu-test.service"
    runner = etc_root / "veyra-vforge-qemu-test-runner"
    for path, content, mode in (
        (unit, test_runner_unit(), 0o644),
        (runner, test_runner_script(), 0o755),
    ):
        if path.is_symlink():
            raise VforgeQemuError(f"guest test service path may not be a symlink: {path}")
        if path.exists():
            raise VforgeQemuError(f"guest test service already exists in the disposable image: {path}")
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, mode)
        os.chown(path, 0, 0)
    link = wants / "vforge-qemu-test.service"
    if link.exists() or link.is_symlink():
        raise VforgeQemuError("guest already has a Vforge QEMU service enablement link")
    link.symlink_to("../vforge-qemu-test.service")


def find_mount_helper() -> tuple[str, str]:
    fuse2fs = shutil.which("fuse2fs")
    fusermount = shutil.which("fusermount3") or shutil.which("fusermount")
    if fuse2fs is None or fusermount is None:
        raise VforgeQemuError("rootless guest preparation requires fuse2fs and fusermount3")
    return fuse2fs, fusermount


@contextlib.contextmanager
def mounted_ext4(image: pathlib.Path, offset: int, mountpoint: pathlib.Path):
    fuse2fs, fusermount = find_mount_helper()
    mountpoint.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [fuse2fs, "-o", f"offset={offset},fakeroot", str(image), str(mountpoint)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not os.path.ismount(mountpoint):
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if not os.path.ismount(mountpoint):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            log.seek(0)
            detail = log.read().decode(errors="replace")[-1000:]
            raise VforgeQemuError(f"could not mount ext4 guest partition at offset {offset}" + (f": {detail.strip()}" if detail.strip() else ""))
        try:
            yield mountpoint
        finally:
            result = subprocess.run([fusermount, "-u", str(mountpoint)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                process.terminate()
                raise VforgeQemuError(f"could not unmount guest preparation filesystem: {result.stderr.strip()}")
            process.wait(timeout=5)


def load_uki_patcher():
    path = ROOT / "scripts/test-sysupdate-image.py"
    spec = importlib.util.spec_from_file_location("vforge_sysupdate_image", path)
    if spec is None or spec.loader is None:
        raise VforgeQemuError("could not load the existing UKI fixture helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch_uki


def prepare_test_image(base_image: pathlib.Path, output_dir: pathlib.Path, offsets: dict[str, tuple[int, int]], image_version: str, roothash: str) -> pathlib.Path:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    image = output_dir / "veyra-vforge-test.raw"
    if image.exists() or image.is_symlink():
        raise VforgeQemuError(f"refusing to overwrite disposable test image: {image}")
    run(["cp", "--sparse=always", str(base_image), str(image)])

    etc_mount = output_dir / "mount-etc"
    home_mount = output_dir / "mount-home"
    try:
        with mounted_ext4(image, offsets["VEYRA_ETC"][0], etc_mount) as etc_root:
            with mounted_ext4(image, offsets["home"][0], home_mount) as home_root:
                inject_test_machine_id(etc_root)
                inject_test_user(etc_root, home_root)
                write_guest_test_service(etc_root)
    finally:
        etc_mount.rmdir() if etc_mount.exists() else None
        home_mount.rmdir() if home_mount.exists() else None

    mcopy = shutil.which("mcopy")
    if mcopy is None:
        raise VforgeQemuError("mtools mcopy is required to install the test-only serial UKI")
    esp_offset = offsets["esp"][0]
    version_match = re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", image_version)
    if not version_match:
        raise VforgeQemuError(f"unsupported Veyra image version for test boot: {image_version!r}")
    entry_name = f"veyra-next_{image_version}.efi"
    source_uki = base_image.with_name(entry_name)
    if not source_uki.is_file():
        raise VforgeQemuError(f"missing active Veyra UKI: {source_uki}")
    patched_uki = output_dir / "veyra-vforge-test.efi"
    load_uki_patcher()(
        source_uki, patched_uki, image_version, roothash, "multi-user.target", version_id="44",
    )
    run([mcopy, "-o", "-i", f"{image}@@{esp_offset}", str(patched_uki), f"::/EFI/Linux/{entry_name}"])
    return image


def find_ovmf_pair() -> tuple[pathlib.Path, pathlib.Path]:
    pairs = (
        (pathlib.Path("/usr/share/edk2/ovmf/OVMF_CODE.fd"), pathlib.Path("/usr/share/edk2/ovmf/OVMF_VARS.fd")),
        (pathlib.Path("/usr/share/OVMF/OVMF_CODE.fd"), pathlib.Path("/usr/share/OVMF/OVMF_VARS.fd")),
        (pathlib.Path("/usr/share/OVMF/OVMF_CODE_4M.fd"), pathlib.Path("/usr/share/OVMF/OVMF_VARS_4M.fd")),
    )
    for code, variables in pairs:
        if code.is_file() and variables.is_file():
            return code, variables
    raise VforgeQemuError("OVMF_CODE and OVMF_VARS are required for the Fedora 44 guest boot")


def require_kvm() -> str:
    if os.geteuid() == 0:
        raise VforgeQemuError("run the host-side QEMU launcher as a normal user")
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        raise VforgeQemuError("qemu-system-x86_64 is not installed")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise VforgeQemuError("/dev/kvm is not accessible; this scenario requires QEMU/KVM")
    accelerators = run([qemu, "-accel", "help"], capture=True, check=False)
    if "kvm" not in (accelerators.stdout or "") + (accelerators.stderr or ""):
        raise VforgeQemuError("this QEMU build does not provide KVM acceleration")
    return qemu


def qemu_command(
    qemu: str, image: pathlib.Path, source_root: pathlib.Path, code: pathlib.Path,
    variables: pathlib.Path, vars_copy: pathlib.Path, serial_log: pathlib.Path,
) -> list[str]:
    values = (image, source_root, code, vars_copy, serial_log)
    if any("," in str(path) for path in values):
        raise VforgeQemuError("QEMU file/share paths containing commas are not supported")
    shutil.copyfile(variables, vars_copy)
    return [
        qemu, "-machine", "q35,accel=kvm", "-cpu", "host", "-smp", "2", "-m", "4096",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
        "-drive", f"if=none,id=systemdisk,format=raw,file={image}",
        "-device", "virtio-blk-pci,drive=systemdisk,bootindex=1",
        "-netdev", "user,id=net0",
        "-device", "virtio-net-pci,netdev=net0",
        "-virtfs", f"local,id=vforge-source,path={source_root},mount_tag=vforge-source,security_model=none,readonly=on",
        "-display", "none", "-serial", f"file:{serial_log}", "-monitor", "none", "-no-reboot",
    ]


def run_guest(qemu: str, image: pathlib.Path, source_root: pathlib.Path, output_dir: pathlib.Path, timeout: int) -> str:
    code, variables = find_ovmf_pair()
    serial_log = output_dir / "serial.log"
    qemu_log = output_dir / "qemu.log"
    command = qemu_command(qemu, image, source_root, code, variables, output_dir / "OVMF_VARS.fd", serial_log)
    with qemu_log.open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                try:
                    serial = serial_log.read_text(encoding="utf-8", errors="replace")
                except FileNotFoundError:
                    serial = ""
                if PASS_MARKER in serial:
                    try:
                        process.wait(timeout=60)
                    except subprocess.TimeoutExpired as error:
                        raise VforgeQemuError(
                            "guest reported PASS but QEMU did not power off within 60s:\n"
                            + "\n".join(serial.splitlines()[-120:])
                        ) from error
                    return serial
                if FAIL_MARKER in serial:
                    raise VforgeQemuError("guest Vforge scenario reported failure:\n" + "\n".join(serial.splitlines()[-120:]))
                if process.poll() is not None:
                    raise VforgeQemuError(f"QEMU exited with status {process.returncode} before the Vforge pass marker")
                time.sleep(2)
            raise VforgeQemuError(f"QEMU guest did not finish within {timeout}s")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path, help="validated Fedora 44 Veyra raw image with sidecar metadata")
    parser.add_argument("--source-root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--work-dir", type=pathlib.Path, default=ROOT / "work/vforge-qemu")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    try:
        qemu = require_kvm()
        source_root = args.source_root.resolve(strict=True)
        if not (source_root / "scripts/test-vforge-qemu.sh").is_file():
            raise VforgeQemuError(f"source checkout has no Vforge guest scenario: {source_root}")
        if "," in str(source_root):
            raise VforgeQemuError("source checkout path containing a comma cannot be shared through QEMU 9p")
        base_image = args.image.resolve(strict=True)
        offsets, version, roothash = validate_base_image(base_image)
        if args.timeout <= 0:
            raise VforgeQemuError("QEMU timeout must be a positive number of seconds")
        output_dir = args.work_dir / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        output_dir.mkdir(mode=0o700, parents=True)
        test_image = prepare_test_image(base_image, output_dir, offsets, version, roothash)
        serial = run_guest(qemu, test_image, source_root, output_dir, args.timeout)
        print(f"Vforge Fedora 44 guest scenario passed. Serial log: {output_dir / 'serial.log'}", flush=True)
        print("\n".join(serial.splitlines()[-40:]), flush=True)
        return 0
    except (OSError, VforgeQemuError, subprocess.CalledProcessError) as error:
        print(f"Vforge QEMU runner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
