#!/usr/bin/env python3
"""Exercise signed OTA install, preflight, blessing, and rollback inside QEMU."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import uuid


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def next_version(version: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise RuntimeError(f"CI slot test expects a numeric image version, got {version!r}.")
    components = version.split(".")
    components[-1] = str(int(components[-1]) + 1)
    return ".".join(components)


def write_xz(source: pathlib.Path, destination: pathlib.Path) -> None:
    # Ubuntu 24 CI uses systemd 255, whose importer supports xz but not zstd.
    # Fedora 44's systemd 259 handles both; xz lets this signed fixture run on
    # both versions without changing the production zstd release format.
    with destination.open("wb") as output:
        subprocess.run(["xz", "-T0", "-3", "-c", str(source)], check=True, stdout=output)


def write_sparse_decompressed(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Decompress a root asset while keeping its unused all-zero regions sparse."""
    total = 0
    with subprocess.Popen(["zstd", "-d", "-c", str(source)], stdout=subprocess.PIPE) as decoder:
        if decoder.stdout is None:
            raise RuntimeError("could not open zstd output stream")
        with destination.open("wb") as output:
            while block := decoder.stdout.read(1024 * 1024):
                total += len(block)
                if not any(block):
                    output.seek(len(block), 1)
                else:
                    output.write(block)
            output.truncate(total)
        if decoder.wait() != 0:
            raise RuntimeError(f"could not decompress root image {source}")


def pe_sections(image: pathlib.Path) -> dict[bytes, tuple[int, int, int]]:
    """Return PE section name -> (header offset, raw offset, raw size)."""
    with image.open("rb") as stream:
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"{image} is not a PE image")
        stream.seek(pe_offset + 6)
        section_count = struct.unpack("<H", stream.read(2))[0]
        stream.seek(pe_offset + 20)
        optional_size = struct.unpack("<H", stream.read(2))[0]
        section_table = pe_offset + 24 + optional_size
        result: dict[bytes, tuple[int, int, int]] = {}
        for index in range(section_count):
            header = section_table + 40 * index
            stream.seek(header)
            name = stream.read(8).split(b"\0", 1)[0]
            stream.seek(header + 16)
            raw_size, raw_offset = struct.unpack("<II", stream.read(8))
            result[name] = (header, raw_offset, raw_size)
        return result


def patch_uki(
    source: pathlib.Path,
    destination: pathlib.Path,
    version: str,
    roothash: str,
    unit: str,
    *,
    version_id: str | None = None,
    extra_cmdline: tuple[str, ...] = (),
) -> None:
    """Create a test UKI with a root hash and boot target in its PE sections."""
    if not re.fullmatch(r"[0-9a-f]{64}", roothash):
        raise RuntimeError(f"invalid dm-verity root hash: {roothash!r}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise RuntimeError(f"invalid test UKI version: {version!r}")
    shutil.copyfile(source, destination)
    sections = pe_sections(destination)
    if not {b".cmdline", b".osrel"}.issubset(sections):
        raise RuntimeError("test UKI must contain .cmdline and .osrel sections")

    if any(not re.fullmatch(r"[A-Za-z0-9_.=-]+", argument) for argument in extra_cmdline):
        raise RuntimeError("extra UKI command-line arguments must be simple key=value tokens")
    cmdline = " ".join((
        f"roothash={roothash}",
        "systemd.firstboot=no",
        f"systemd.unit={unit}",
        "console=ttyS0,115200n8",
        "systemd.show_status=1",
        "systemd.log_target=console",
        *extra_cmdline,
    )).encode("ascii") + b"\0"
    with destination.open("r+b") as stream:
        header, raw_offset, raw_size = sections[b".cmdline"]
        if len(cmdline) > raw_size:
            raise RuntimeError("test kernel command line exceeds the UKI .cmdline section")
        stream.seek(raw_offset)
        stream.write(cmdline.ljust(raw_size, b"\0"))
        stream.seek(header + 8)
        stream.write(struct.pack("<I", len(cmdline)))

        header, raw_offset, raw_size = sections[b".osrel"]
        stream.seek(header + 8)
        virtual_size = struct.unpack("<I", stream.read(4))[0]
        stream.seek(raw_offset)
        os_release = stream.read(virtual_size)
        os_release, version_matches = re.subn(
            rb"(?m)^IMAGE_VERSION=.*$",
            f'IMAGE_VERSION="{version}"'.encode("ascii"),
            os_release,
        )
        if version_matches != 1:
            raise RuntimeError("test UKI .osrel must contain exactly one IMAGE_VERSION")
        if version_id is not None:
            os_release, id_matches = re.subn(
                rb"(?m)^VERSION_ID=.*$",
                f'VERSION_ID={version_id}'.encode("ascii"),
                os_release,
            )
            if id_matches != 1:
                raise RuntimeError("test UKI .osrel must contain exactly one VERSION_ID")
        if len(os_release) > raw_size:
            raise RuntimeError("test UKI .osrel exceeds its existing PE section")
        stream.seek(raw_offset)
        stream.write(os_release.ljust(raw_size, b"\0"))
        stream.seek(header + 8)
        stream.write(struct.pack("<I", len(os_release)))


def append_uki_cmdline(uki: pathlib.Path, *arguments: str) -> None:
    """Append test-only boot arguments to an existing UKI without changing identity."""
    if any(not re.fullmatch(r"[A-Za-z0-9_.,=-]+", argument) for argument in arguments):
        raise RuntimeError("UKI command-line arguments must be simple key=value tokens")
    sections = pe_sections(uki)
    if b".cmdline" not in sections:
        raise RuntimeError(f"test UKI {uki} has no .cmdline section")
    header, raw_offset, raw_size = sections[b".cmdline"]
    with uki.open("r+b") as stream:
        stream.seek(raw_offset)
        old = stream.read(raw_size).split(b"\0", 1)[0].decode("ascii")
        values = old.split()
        for argument in arguments:
            key = argument.split("=", 1)[0]
            values = [item for item in values if item.split("=", 1)[0] != key]
            values.append(argument)
        cmdline = (" ".join(values)).encode("ascii") + b"\0"
        if len(cmdline) > raw_size:
            raise RuntimeError("test UKI command line exceeds its existing PE section")
        stream.seek(raw_offset)
        stream.write(cmdline.ljust(raw_size, b"\0"))
        stream.seek(header + 8)
        stream.write(struct.pack("<I", len(cmdline)))


def add_serial_to_initial_uki(image: pathlib.Path) -> None:
    """Make the initial, test-copy boot visible on serial before the OTA UKI exists."""
    table = json.loads(run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout)["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    esp_type = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
    esps = [p for p in table["partitions"] if str(p.get("type", "")).lower() == esp_type]
    if len(esps) != 1:
        raise RuntimeError("the test image must contain exactly one EFI system partition")
    esp_offset = int(esps[0]["start"]) * sector_size
    image_ref = f"{image}@@{esp_offset}"
    listing = run(["mdir", "-b", "-i", image_ref, "::/EFI/Linux"], stdout=subprocess.PIPE).stdout
    names = [
        pathlib.PurePosixPath(line.strip()).name
        for line in listing.splitlines()
        if line.strip().lower().endswith(".efi")
    ]
    if len(names) != 1:
        raise RuntimeError(f"expected one initial UKI in EFI/Linux, found {names!r}")
    with tempfile.TemporaryDirectory(prefix="veyra-serial-uki-") as temporary:
        uki = pathlib.Path(temporary) / "initial.efi"
        run(["mcopy", "-i", image_ref, f"::/EFI/Linux/{names[0]}", str(uki)])
        append_uki_cmdline(
            uki,
            "systemd.unit=veyra-ota-ci.target",
            "console=ttyS0,115200n8",
            "systemd.firstboot=no",
            "systemd.show_status=1",
            "systemd.log_target=console",
        )
        run(["mcopy", "-o", "-i", image_ref, str(uki), f"::/EFI/Linux/{names[0]}"])


def os_release_value(uki: pathlib.Path, key: str) -> str:
    sections = pe_sections(uki)
    if b".osrel" not in sections:
        raise RuntimeError(f"UKI {uki} has no .osrel section")
    _, raw_offset, _ = sections[b".osrel"]
    with uki.open("rb") as stream:
        stream.seek(raw_offset)
        data = stream.read(65536).split(b"\0", 1)[0].decode("utf-8", errors="strict")
    match = re.search(rf"(?m)^{re.escape(key)}=(.*)$", data)
    if not match:
        raise RuntimeError(f"UKI {uki} .osrel has no {key}")
    return match.group(1).strip().strip('"')


def write_manifest(source: pathlib.Path, assets: list[str], fingerprint: str) -> None:
    lines = []
    for name in sorted(assets):
        digest = hashlib.sha256()
        with (source / name).open("rb") as asset:
            for block in iter(lambda: asset.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {name}")
    manifest = source / "SHA256SUMS"
    manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
    run([
        "gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
        "--local-user", fingerprint,
        "--detach-sign", "--output", str(source / "SHA256SUMS.gpg"), str(manifest),
    ])


def write_test_definitions(
    source: pathlib.Path,
    definitions: pathlib.Path,
    base_url: str,
    firmware: str = "uefi",
) -> pathlib.Path:
    destination = source / "definitions"
    destination.mkdir()
    expected = {
        "10-root-verity.transfer",
        "20-root.transfer",
        "80-bios-kernel.transfer",
        "90-bios-initrd.transfer",
        "99-uki.transfer",
    }
    actual = {path.name for path in definitions.glob("*.transfer")}
    if actual != expected:
        raise RuntimeError(f"Unexpected system update transfer set: {sorted(actual)}")
    transfers_by_firmware = {
        "uefi": {"10-root-verity.transfer", "20-root.transfer", "99-uki.transfer"},
        "bios": {
            "10-root-verity.transfer",
            "20-root.transfer",
            "80-bios-kernel.transfer",
            "90-bios-initrd.transfer",
        },
    }
    if firmware not in transfers_by_firmware:
        raise RuntimeError(f"unsupported update test firmware: {firmware!r}")
    for name in sorted(transfers_by_firmware[firmware]):
        content = (definitions / name).read_text(encoding="utf-8")
        content, replacements = re.subn(
            r"(?m)^Path=https://github\.com/angyedz/Veyra-Linux-next/releases/latest/download/?[ \t]*$",
            f"Path={base_url}",
            content,
        )
        if replacements != 1:
            raise RuntimeError(f"Could not redirect the release source in {name}.")
        content = content.replace(".root-verity.raw.zst", ".root-verity.raw.xz")
        content = content.replace(".root.raw.zst", ".root.raw.xz")
        # Ubuntu 24.04 CI ships systemd 255, which still enumerates .conf
        # definitions. Fedora 44's systemd 259 reads .transfer and retains
        # .conf as a backwards-compatible suffix.
        (destination / f"{pathlib.Path(name).stem}.conf").write_text(
            content, encoding="utf-8"
        )
    return destination


def inspect_partitions(image: pathlib.Path) -> tuple[list[dict[str, object]], int]:
    layout = run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
    partition_table = json.loads(layout)["partitiontable"]
    return partition_table["partitions"], int(partition_table.get("sectorsize", 512))


def require_partition_identity(
    partitions: list[dict[str, object]], label: str, expected_uuid: str,
    *, read_only: bool = False,
) -> None:
    matches = [partition for partition in partitions if partition.get("name") == label]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one staged partition named {label}, found {len(matches)}.")
    actual_uuid = str(matches[0].get("uuid", "")).replace("-", "").lower()
    if actual_uuid != expected_uuid.replace("-", "").lower():
        raise RuntimeError(f"Staged partition {label} has the wrong UUID.")
    if read_only and "GUID:60" not in str(matches[0].get("attrs", "")):
        raise RuntimeError(f"Staged partition {label} lost the GPT read-only attribute.")


def inject_guest_ota_test(
    image: pathlib.Path,
    definitions: pathlib.Path,
    exported_key: pathlib.Path,
    old_version: str,
    version: str,
    roothash: str,
    marker: str,
    run_vpkg_ci: bool,
) -> None:
    """Configure only the disposable guest image to run the updater test on boot."""
    partitions, sector_size = inspect_partitions(image)
    matches = [partition for partition in partitions if partition.get("name") == "VEYRA_ETC"]
    if len(matches) != 1:
        raise RuntimeError("the test image must contain exactly one persistent /etc partition")
    partition = matches[0]
    offset = int(partition["start"]) * sector_size
    size = int(partition["size"]) * sector_size

    with tempfile.TemporaryDirectory(prefix="veyra-ota-ci-etc-") as temporary:
        work = pathlib.Path(temporary)
        mountpoint = work / "etc"
        mountpoint.mkdir()
        run([
            "sudo", "mount", "-t", "ext4", "-o",
            f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
            str(image), str(mountpoint),
        ])
        try:
            if run_vpkg_ci:
                (mountpoint / "veyra/vpkg-ci-enabled").parent.mkdir(parents=True, exist_ok=True)
                (mountpoint / "veyra/vpkg-ci-enabled").write_text("enabled\n", encoding="ascii")
            systemd_definitions = mountpoint / "sysupdate.d"
            systemd_definitions.mkdir(parents=True, exist_ok=True)
            for source in sorted(definitions.glob("*.conf")):
                destination = systemd_definitions / f"{source.stem}.transfer"
                shutil.copyfile(source, destination)
                destination.chmod(0o644)
            keyring = mountpoint / "systemd/import-pubring.gpg"
            keyring.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(exported_key, keyring)
            keyring.chmod(0o644)

            test_dir = mountpoint / "veyra/ota-ci"
            test_dir.mkdir(parents=True, exist_ok=True)
            test_script = test_dir / "prepare"
            test_script.write_text(
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                "state=/var/lib/veyra/ota-ci\n"
                "intents=/var/lib/veyra/vpkg/intents.json\n"
                "pending=/var/lib/veyra/vpkg/pending.json\n"
                f"version={version!r}\n"
                f"old_version={old_version!r}\n"
                f"roothash={roothash!r}\n"
                f"marker={marker!r}\n"
                "mkdir -p \"$state\" \"$(dirname \"$intents\")\"\n"
                "saved_intents=\"$state/intents.before-conflict.json\"\n"
                "had_intents=0\n"
                "if [[ -s \"$intents\" ]]; then cp -a \"$intents\" \"$saved_intents\"; had_intents=1; fi\n"
                "printf '{\"schema\":1,\"requests\":[{\"kind\":\"repo\",\"name\":\"systemd\"}]}\\n' >\"$intents\"\n"
                "if /usr/bin/veyra-update install \"$version\" >\"$state/conflict.log\" 2>&1; then\n"
                "  cat \"$state/conflict.log\" >&2\n"
                "  echo 'OTA preflight unexpectedly accepted a protected base-system package.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'no trial UKI was installed' \"$state/conflict.log\"\n"
                "test ! -e \"$pending\"\n"
                f"test ! -e /boot/EFI/Linux/veyra-next_{version}+3-0.efi\n"
                "/usr/bin/veyra-update pending >\"$state/no-pending.log\"\n"
                "grep -Fq 'No Veyra system update is waiting for a reboot.' \"$state/no-pending.log\"\n"
                "if /usr/bin/veyra-update reboot >\"$state/no-reboot.log\" 2>&1; then\n"
                "  echo 'veyra-update reboot accepted a root-only partial stage.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'There is no completed Veyra system update' \"$state/no-reboot.log\"\n"
                "if (( had_intents )); then cp -a \"$saved_intents\" \"$intents\"; else printf '{\"schema\":1,\"requests\":[]}\\n' >\"$intents\"; fi\n"
                "/usr/bin/veyra-update install \"$version\"\n"
                "python3 - \"$pending\" \"$version\" \"$roothash\" <<'PY'\n"
                "import json, pathlib, sys\n"
                "value = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "assert value == {'schema': 1, 'target_roothash': sys.argv[3], 'version': sys.argv[2]}\n"
                "PY\n"
                "if /usr/bin/vpkg install htop >\"$state/mutation.log\" 2>&1; then\n"
                "  echo 'vpkg unexpectedly allowed a mutation while OTA was pending.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'Package changes are blocked while OTA' \"$state/mutation.log\"\n"
                "/usr/bin/veyra-update pending >\"$state/pending.log\"\n"
                f"grep -Fq 'Veyra {version} is staged as a signed trial UKI' \"$state/pending.log\"\n"
                "touch \"$state/staged\"\n"
                "echo \"VEYRA_OTA_PREPARE_CI_PASS_$marker\"\n"
                "/usr/bin/bootctl set-oneshot \"veyra-next_${old_version}.efi\"\n"
                "/usr/bin/systemctl reboot\n",
                encoding="ascii",
            )
            test_script.chmod(0o755)
            success_script = test_dir / "success"
            success_script.write_text(
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                "test ! -e /var/lib/veyra/vpkg/pending.json\n"
                "stub=\"$(bootctl --print-stub-path)\"\n"
                "python3 - \"$stub\" "
                f"{version!r} {roothash!r} <<'PY'\n"
                "import pathlib, re, struct, sys\n"
                "path = pathlib.Path(sys.argv[1])\n"
                "with path.open('rb') as stream:\n"
                "    stream.seek(0x3c); pe = struct.unpack('<I', stream.read(4))[0]\n"
                "    stream.seek(pe); assert stream.read(4) == b'PE\\0\\0'\n"
                "    stream.seek(pe + 6); count = struct.unpack('<H', stream.read(2))[0]\n"
                "    stream.seek(pe + 20); optional = struct.unpack('<H', stream.read(2))[0]\n"
                "    table = pe + 24 + optional; sections = {}\n"
                "    for index in range(count):\n"
                "        stream.seek(table + index * 40); name = stream.read(8).split(b'\\0', 1)[0]\n"
                "        stream.seek(table + index * 40 + 16); size, offset = struct.unpack('<II', stream.read(8))\n"
                "        sections[name] = (offset, size)\n"
                "    osrel_offset, osrel_size = sections[b'.osrel']; cmd_offset, cmd_size = sections[b'.cmdline']\n"
                "    stream.seek(osrel_offset); osrel = stream.read(osrel_size).split(b'\\0', 1)[0].decode()\n"
                "    stream.seek(cmd_offset); cmdline = stream.read(cmd_size).split(b'\\0', 1)[0].decode()\n"
                "versions = re.findall(r'(?m)^IMAGE_VERSION=(.*)$', osrel)\n"
                "hashes = re.findall(r'(?:^|\\s)roothash=([0-9a-f]{64})(?:\\s|$)', cmdline)\n"
                "assert len(versions) == 1 and versions[0].strip().strip('\\\"') == sys.argv[2]\n"
                "assert hashes == [sys.argv[3]]\n"
                "PY\n"
                f"echo VEYRA_OTA_SUCCESS_CI_PASS_{marker}\n",
                encoding="ascii",
            )
            success_script.chmod(0o755)
            manual_old_script = test_dir / "manual-old"
            manual_old_script.write_text(
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                "state=/var/lib/veyra/ota-ci\n"
                "pending=/var/lib/veyra/vpkg/pending.json\n"
                "test -e \"$pending\"\n"
                "test ! -e \"$state/manual-old-checked\"\n"
                "if /usr/bin/vpkg install htop >\"$state/manual-old-mutation.log\" 2>&1; then\n"
                "  echo 'vpkg unexpectedly allowed a mutation on a manually selected old UKI.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'Package changes are blocked while OTA' \"$state/manual-old-mutation.log\"\n"
                "/usr/bin/veyra-update pending >\"$state/manual-old-pending.log\"\n"
                f"grep -Fq 'Veyra {version} is staged as a signed trial UKI' \"$state/manual-old-pending.log\"\n"
                "touch \"$state/manual-old-checked\"\n"
                f"echo VEYRA_OTA_MANUAL_OLD_CI_PASS_{marker}\n"
                "/usr/bin/veyra-update reboot\n",
                encoding="ascii",
            )
            manual_old_script.chmod(0o755)

            systemd = mountpoint / "systemd/system"
            systemd.mkdir(parents=True, exist_ok=True)
            vpkg_wants = (
                "veyra-vpkg-stage-recovery.service veyra-vpkg-ci.service "
                if run_vpkg_ci else ""
            )
            vpkg_after = " veyra-vpkg-ci.service" if run_vpkg_ci else ""
            prepare_after = "graphical.target veyra-vpkg-ci.service" if run_vpkg_ci else "graphical.target"
            vpkg_stage_condition = (
                "ConditionPathExists=/var/lib/veyra/vpkg-ci-stage-pass\n\n"
                if run_vpkg_ci else ""
            )
            (systemd / "veyra-ota-ci.target").write_text(
                "[Unit]\n"
                "Description=Disposable Veyra OTA integration test sequence\n"
                "Requires=graphical.target\n"
                f"After=graphical.target{vpkg_after}\n"
                f"Wants={vpkg_wants}veyra-ota-ci-prepare.service veyra-ota-ci-manual-old.service\n",
                encoding="ascii",
            )
            diagnostic_script = test_dir / "bootcount-check.py"
            diagnostic_script.write_text(
                "#!/usr/bin/python3\n"
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"expected = {f'veyra-next_{version}+3-0.efi'!r}\n"
                "status = subprocess.run(['/usr/bin/bootctl', 'status'], check=True, text=True, capture_output=True)\n"
                "print(status.stdout, end='')\n"
                "root = Path('/sys/firmware/efi/efivars')\n"
                "variables = sorted(root.glob('LoaderBootCountPath-*')) + sorted(root.glob('LoaderEntrySelected-*'))\n"
                "decoded = []\n"
                "for path in variables:\n"
                "    value = path.read_bytes()[4:].decode('utf-16-le', errors='replace').rstrip('\\0')\n"
                "    print(f'{path.name}={value}')\n"
                "    decoded.append(value)\n"
                "if not any(expected.lower() in value.lower() for value in decoded):\n"
                "    print(f'VEYRA_OTA_BOOTCOUNT_DIAGNOSTICS_FAIL expected={expected!r}', flush=True)\n"
                "    sys.exit(1)\n"
                "print('VEYRA_OTA_BOOTCOUNT_DIAGNOSTICS', flush=True)\n",
                encoding="ascii",
            )
            diagnostic_script.chmod(0o755)
            diagnostic_exec = systemd / "veyra-ota-ci-diagnostics.service"
            diagnostic_exec.write_text(
                "[Unit]\n"
                "Description=Check systemd-boot trial count metadata\n"
                "After=graphical.target\n"
                "Before=boot-complete.target\n"
                "ConditionKernelCommandLine=veyra.ota-ci=trial\n\n"
                "[Service]\nType=oneshot\n"
                "ExecStart=/etc/veyra/ota-ci/bootcount-check.py\n"
                "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            (systemd / "veyra-ota-ci-prepare.service").write_text(
                "[Unit]\n"
                "Description=Exercise Veyra OTA preflight inside the QEMU guest\n"
                f"After={prepare_after}\n"
                "Before=veyra-ota-ci.target\n"
                "ConditionKernelCommandLine=!veyra.ota-ci=trial\n\n"
                "ConditionPathExists=!/var/lib/veyra/ota-ci/staged\n\n"
                f"{vpkg_stage_condition}"
                "[Service]\nType=oneshot\n"
                "ExecStart=/etc/veyra/ota-ci/prepare\n"
                "TimeoutStartSec=600\n"
                "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            (systemd / "veyra-ota-ci-success.service").write_text(
                "[Unit]\n"
                "Description=Verify successful Veyra OTA completion inside the QEMU guest\n"
                "After=systemd-bless-boot.service\n"
                "ConditionKernelCommandLine=veyra.ota-ci=trial\n\n"
                "[Service]\nType=oneshot\n"
                "ExecStart=/etc/veyra/ota-ci/success\n"
                "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            (systemd / "veyra-ota-ci-manual-old.service").write_text(
                "[Unit]\n"
                "Description=Verify an old UKI remains healthy while an OTA trial is available\n"
                f"After=graphical.target{vpkg_after} veyra-ota-ci-prepare.service\n"
                "Before=veyra-ota-ci.target\n"
                "ConditionKernelCommandLine=!veyra.ota-ci=trial\n"
                "ConditionPathExists=/var/lib/veyra/ota-ci/staged\n"
                "ConditionPathExists=!/var/lib/veyra/ota-ci/manual-old-checked\n\n"
                "[Service]\nType=oneshot\n"
                "ExecStart=/etc/veyra/ota-ci/manual-old\n"
                "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            if run_vpkg_ci:
                vpkg_dropin = systemd / "veyra-vpkg-ci.service.d/veyra-ota-ci.conf"
                vpkg_dropin.parent.mkdir(parents=True, exist_ok=True)
                vpkg_dropin.write_text(
                    "[Unit]\nConditionPathExists=/etc/veyra/vpkg-ci-enabled\n",
                    encoding="ascii",
                )
            (mountpoint / "systemd/system/boot-complete.target.d").mkdir(
                parents=True, exist_ok=True
            )
            (mountpoint / "systemd/system/boot-complete.target.d/veyra-ota-ci.conf").write_text(
                "[Unit]\nRequires=veyra-ota-ci-diagnostics.service\n"
                "After=veyra-ota-ci-diagnostics.service\n",
                encoding="ascii",
            )
            (mountpoint / "systemd/system/systemd-bless-boot.service.d").mkdir(
                parents=True, exist_ok=True
            )
            (mountpoint / "systemd/system/systemd-bless-boot.service.d/veyra-ota-ci.conf").write_text(
                "[Unit]\nWants=veyra-ota-ci-success.service\n",
                encoding="ascii",
            )
        finally:
            run(["sudo", "umount", str(mountpoint)])


def inject_bios_guest_ota_test(
    image: pathlib.Path,
    definitions: pathlib.Path,
    exported_key: pathlib.Path,
    version: str,
    roothash: str,
    marker: str,
    run_vpkg_ci: bool,
) -> None:
    """Configure a disposable Legacy BIOS guest to stage and bless a signed OTA."""
    partitions, sector_size = inspect_partitions(image)
    etc_partitions = [p for p in partitions if p.get("name") == "VEYRA_ETC"]
    if len(etc_partitions) != 1:
        raise RuntimeError("the test image must contain exactly one persistent /etc partition")
    partition = etc_partitions[0]
    offset = int(partition["start"]) * sector_size
    size = int(partition["size"]) * sector_size

    with tempfile.TemporaryDirectory(prefix="veyra-bios-ota-ci-etc-") as temporary:
        work = pathlib.Path(temporary)
        mountpoint = work / "etc"
        mountpoint.mkdir()
        run([
            "sudo", "mount", "-t", "ext4", "-o",
            f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
            str(image), str(mountpoint),
        ])
        try:
            if run_vpkg_ci:
                marker_file = mountpoint / "veyra/vpkg-ci-enabled"
                marker_file.parent.mkdir(parents=True, exist_ok=True)
                marker_file.write_text("enabled\n", encoding="ascii")
            systemd_definitions = mountpoint / "sysupdate.d"
            systemd_definitions.mkdir(parents=True, exist_ok=True)
            for source in sorted(definitions.glob("*.conf")):
                destination = systemd_definitions / f"{source.stem}.transfer"
                shutil.copyfile(source, destination)
                destination.chmod(0o644)
            keyring = mountpoint / "systemd/import-pubring.gpg"
            keyring.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(exported_key, keyring)
            keyring.chmod(0o644)

            test_dir = mountpoint / "veyra/ota-ci"
            test_dir.mkdir(parents=True, exist_ok=True)
            prepare = test_dir / "prepare-bios"
            prepare.write_text(
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                "state=/var/lib/veyra/ota-ci\n"
                "intents=/var/lib/veyra/vpkg/intents.json\n"
                "pending=/var/lib/veyra/vpkg/pending.json\n"
                f"version={version!r}\n"
                f"roothash={roothash!r}\n"
                f"marker={marker!r}\n"
                "mkdir -p \"$state\" \"$(dirname \"$intents\")\"\n"
                "saved_intents=\"$state/intents.before-conflict.json\"\n"
                "had_intents=0\n"
                "if [[ -s \"$intents\" ]]; then cp -a \"$intents\" \"$saved_intents\"; had_intents=1; fi\n"
                "printf '{\"schema\":1,\"requests\":[{\"kind\":\"repo\",\"name\":\"systemd\"}]}\\n' >\"$intents\"\n"
                "if /usr/bin/veyra-update install \"$version\" >\"$state/conflict.log\" 2>&1; then\n"
                "  cat \"$state/conflict.log\" >&2\n"
                "  echo 'OTA preflight unexpectedly accepted a protected base-system package.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'no trial boot entry was installed' \"$state/conflict.log\"\n"
                "test ! -e \"$pending\"\n"
                f"test ! -e /boot/VEYRA/veyra-next_{version}.vmlinuz\n"
                f"test ! -e /boot/VEYRA/veyra-next_{version}.initrd\n"
                "/usr/bin/veyra-update pending >\"$state/no-pending.log\"\n"
                "grep -Fq 'No Veyra system update is waiting for a reboot.' \"$state/no-pending.log\"\n"
                "if /usr/bin/veyra-update reboot >\"$state/no-reboot.log\" 2>&1; then\n"
                "  echo 'veyra-update reboot accepted a root-only partial stage.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'There is no completed Veyra system update' \"$state/no-reboot.log\"\n"
                "if (( had_intents )); then cp -a \"$saved_intents\" \"$intents\"; else printf '{\"schema\":1,\"requests\":[]}\\n' >\"$intents\"; fi\n"
                "/usr/bin/veyra-update install \"$version\"\n"
                "python3 - \"$pending\" \"$version\" \"$roothash\" <<'PY'\n"
                "import json, pathlib, sys\n"
                "value = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "assert value == {'schema': 1, 'target_roothash': sys.argv[3], 'version': sys.argv[2]}\n"
                "PY\n"
                "if /usr/bin/vpkg install htop >\"$state/mutation.log\" 2>&1; then\n"
                "  echo 'vpkg unexpectedly allowed a mutation while OTA was pending.' >&2\n"
                "  exit 1\n"
                "fi\n"
                "grep -Fq 'Package changes are blocked while OTA' \"$state/mutation.log\"\n"
                "/usr/bin/veyra-update pending >\"$state/pending.log\"\n"
                f"grep -Fq 'Veyra {version} is staged as a signed GRUB BIOS trial (3 tries remain).' \"$state/pending.log\"\n"
                "touch \"$state/staged\"\n"
                "printf 'VEYRA_BIOS_OTA_PREPARE_CI_PASS_%s\\n' \"$marker\" >/dev/ttyS0\n"
                "/usr/bin/veyra-update reboot\n",
                encoding="ascii",
            )
            prepare.chmod(0o755)

            stable_check = test_dir / "stable-check"
            stable_check_script = (
                "#!/usr/bin/bash\n"
                "set -euo pipefail\n"
                "identity=\"$(/usr/bin/python3 /usr/lib/veyra/grub_ota.py identity)\"\n"
                "state=\"$(/usr/bin/python3 /usr/lib/veyra/grub_ota.py status)\"\n"
                "cmdline=\"$(cat /proc/cmdline)\"\n"
                f"case \" $cmdline \" in *\" roothash={roothash} \"*) ;; *) echo 'BIOS OTA booted with the wrong root hash' >&2; exit 1 ;; esac\n"
                f"case \" $cmdline \" in *\" veyra.image_version={version} \"*) ;; *) echo 'BIOS OTA booted with the wrong image version' >&2; exit 1 ;; esac\n"
                "python3 - \"$identity\" \"$state\" <<'PY'\n"
                "import json, sys\n"
                "identity = sys.argv[1].splitlines()\n"
                "state = json.loads(sys.argv[2])\n"
                "assert len(identity) == 2\n"
                "assert state['veyra_stable_version'] == identity[0]\n"
                "assert state['veyra_stable_roothash'] == identity[1]\n"
                "PY\n"
            )
            if run_vpkg_ci:
                stable_check_script += "test -e /var/lib/veyra/vpkg-ci-complete\n"
            stable_check_script += (
                f"printf 'VEYRA_BIOS_OTA_STABLE_BOOT_CI_PASS_{marker} roothash={roothash} "
                f"veyra.image_version={version}\\n' >/dev/ttyS0\n"
            )
            stable_check.write_text(stable_check_script, encoding="ascii")
            stable_check.chmod(0o755)

            systemd = mountpoint / "systemd/system"
            systemd.mkdir(parents=True, exist_ok=True)
            prepare_unit = systemd / "veyra-ota-ci-prepare.service"
            prepare_after = "graphical.target veyra-vpkg-ci.service" if run_vpkg_ci else "graphical.target"
            prepare_unit.write_text(
                "[Unit]\n"
                "Description=Stage a signed Veyra BIOS OTA from the disposable guest\n"
                + ("Requires=veyra-vpkg-ci.service\n" if run_vpkg_ci else "")
                + f"After={prepare_after}\n"
                + "ConditionKernelCommandLine=!veyra.image_version=" + version + "\n"
                + "ConditionPathExists=!/var/lib/veyra/ota-ci/staged\n\n"
                + "[Service]\nType=oneshot\n"
                + "ExecStart=/etc/veyra/ota-ci/prepare-bios\n"
                + "TimeoutStartSec=600\n"
                + "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            graphical_wants = systemd / "graphical.target.wants"
            graphical_wants.mkdir(parents=True, exist_ok=True)
            (graphical_wants / prepare_unit.name).symlink_to(
                "/etc/systemd/system/" + prepare_unit.name
            )

            stable_unit = systemd / "veyra-bios-ota-ci-stable.service"
            stable_unit.write_text(
                "[Unit]\n"
                "Description=Verify Veyra BIOS OTA settlement state\n"
                + ("Requires=veyra-vpkg-ci.service\nAfter=veyra-vpkg-ci.service " if run_vpkg_ci else "")
                + "After=veyra-vpkg-sync.service veyra-grub-bless.service\n"
                + "Before=boot-complete.target\n"
                + "ConditionPathExists=!/sys/firmware/efi\n"
                + f"ConditionKernelCommandLine=veyra.image_version={version}\n\n"
                + "[Service]\nType=oneshot\n"
                + "ExecStart=/etc/veyra/ota-ci/stable-check\n"
                + "StandardOutput=journal+console\nStandardError=journal+console\n",
                encoding="ascii",
            )
            boot_complete_dropin = systemd / "boot-complete.target.d/veyra-bios-ota-ci.conf"
            boot_complete_dropin.parent.mkdir(parents=True, exist_ok=True)
            boot_complete_dropin.write_text(
                "[Unit]\nRequires=veyra-bios-ota-ci-stable.service\n"
                "After=veyra-bios-ota-ci-stable.service\n",
                encoding="ascii",
            )
        finally:
            run(["sudo", "umount", str(mountpoint)])


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--transfer-dir", required=True, type=pathlib.Path)
    parser.add_argument("--firmware", choices=("uefi", "bios"), default="uefi")
    parser.add_argument("--candidate-dir", type=pathlib.Path)
    parser.add_argument("--vpkg-test-repo", type=pathlib.Path)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--qemu-accel", default="tcg")
    parser.add_argument("--qemu-cpu", default="max")
    parser.add_argument("--qemu-memory-mib", default=4096, type=int)
    parser.add_argument("--qemu-data-dir", type=pathlib.Path)
    parser.add_argument("--log-directory", type=pathlib.Path)
    parser.add_argument("--ovmf-code", type=pathlib.Path)
    parser.add_argument("--ovmf-vars", type=pathlib.Path)
    parser.add_argument("--boot-timeout", type=int, default=300)
    args = parser.parse_args()

    try:
        if args.qemu_memory_mib < 4096:
            raise RuntimeError("OTA boots require at least 4096 MiB so persistent /home growth can finish.")
        version = next_version(args.current_version)
        image = args.image.resolve()
        if not image.is_file():
            raise RuntimeError(f"Missing test image: {image}")
        if not shutil.which(args.qemu):
            raise RuntimeError(f"QEMU executable not found: {args.qemu}")

        if args.firmware == "uefi":
            if not args.ovmf_code or not args.ovmf_vars:
                raise RuntimeError("UEFI OTA integration requires both --ovmf-code and --ovmf-vars")
            payload_dir = image.parent
            payload_version = args.current_version
            root_candidates = sorted(payload_dir.glob(
                f"veyra-next_{payload_version}_*.root.raw.zst"
            ))
            uki_source = payload_dir / f"veyra-next_{payload_version}.efi"
            if len(root_candidates) != 1 or not uki_source.is_file():
                raise RuntimeError("the built image must export one matching root artifact and UKI")
            bios_kernel = None
            bios_initrd = None
        else:
            if not args.candidate_dir:
                raise RuntimeError("BIOS OTA integration requires --candidate-dir with the target-version build")
            payload_dir = args.candidate_dir.resolve()
            payload_version = version
            root_candidates = sorted(payload_dir.glob(
                f"veyra-next_{payload_version}_*.root.raw.zst"
            ))
            bios_kernel = payload_dir / f"veyra-next_{payload_version}.vmlinuz"
            bios_initrd = payload_dir / f"veyra-next_{payload_version}.initrd"
            if (
                len(root_candidates) != 1
                or not bios_kernel.is_file()
                or not bios_initrd.is_file()
            ):
                raise RuntimeError(
                    "the candidate build must export one matching root, kernel, and initramfs"
                )
            osrelease = payload_dir / "veyra-next.osrelease"
            if not osrelease.is_file() or f'IMAGE_VERSION="{version}"' not in osrelease.read_text(encoding="utf-8"):
                raise RuntimeError("the BIOS candidate root does not identify its target image version")
            uki_source = None

        with tempfile.TemporaryDirectory(prefix="veyra-sysupdate-test-") as temporary:
            source = pathlib.Path(temporary)
            staged_image = source / "veyra-ab-test.raw"
            run(["cp", "--reflink=auto", "--sparse=always", str(image), str(staged_image)])
            if args.vpkg_test_repo:
                prepare_vpkg = pathlib.Path(__file__).with_name("prepare-vpkg-image-test.py")
                run([
                    sys.executable,
                    str(prepare_vpkg),
                    "--image", str(staged_image),
                    "--repository", str(args.vpkg_test_repo.resolve()),
                ])
            root_raw = source / "root.raw"
            verity_raw = source / "verity.raw"
            root_hash_path = source / "roothash"
            write_sparse_decompressed(root_candidates[0], root_raw)
            salt = os.urandom(32).hex()
            run([
                "veritysetup", "format", "--format=1", "--hash=sha256",
                "--data-block-size=4096", "--hash-block-size=4096",
                f"--salt={salt}", f"--root-hash-file={root_hash_path}",
                str(root_raw), str(verity_raw),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            roothash = root_hash_path.read_text(encoding="ascii").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", roothash):
                raise RuntimeError(f"invalid generated dm-verity root hash: {roothash!r}")
            if root_raw.stat().st_size > 8 * 1024**3 or verity_raw.stat().st_size > 512 * 1024**2:
                raise RuntimeError("generated root or verity payload exceeds its A/B partition")
            # The partition images have fixed capacities. Padding the hash image
            # lets the partition transfer preserve the full root-verity slot.
            with verity_raw.open("r+b") as output:
                output.truncate(512 * 1024**2)

            root_uuid = str(uuid.UUID(hex=roothash[:32]))
            verity_uuid = str(uuid.UUID(hex=roothash[32:]))

            root_asset = f"veyra-next_{version}_{root_uuid}.root.raw.xz"
            verity_asset = f"veyra-next_{version}_{verity_uuid}.root-verity.raw.xz"
            write_xz(root_raw, source / root_asset)
            write_xz(verity_raw, source / verity_asset)
            payload_assets = [root_asset, verity_asset]
            if args.firmware == "uefi":
                assert uki_source is not None
                uki_asset = f"veyra-next_{version}.efi"
                test_uki = source / uki_asset
                patch_uki(
                    uki_source,
                    test_uki,
                    version,
                    roothash,
                    "boot-complete.target",
                    extra_cmdline=("veyra.ota-ci=trial",),
                )
                payload_assets.append(uki_asset)
            else:
                assert bios_kernel is not None and bios_initrd is not None
                for artifact in (bios_kernel, bios_initrd):
                    destination = source / artifact.name
                    shutil.copyfile(artifact, destination)
                    if not destination.stat().st_size:
                        raise RuntimeError(f"empty BIOS update artifact: {artifact}")
                    payload_assets.append(destination.name)
            write_manifest(source, payload_assets, args.fingerprint)

            exported_key = source / "update-key.gpg"
            with exported_key.open("wb") as output:
                subprocess.run(
                    ["gpg", "--batch", "--export", args.fingerprint],
                    check=True,
                    stdout=output,
                )

            handler = functools.partial(QuietHandler, directory=str(source))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                definitions = write_test_definitions(
                    source,
                    args.transfer_dir,
                    f"http://10.0.2.100:{args.port}/",
                    firmware=args.firmware,
                )
                ota_marker = uuid.uuid4().hex
                if args.firmware == "uefi":
                    inject_guest_ota_test(
                        staged_image,
                        definitions,
                        exported_key,
                        args.current_version,
                        version,
                        roothash,
                        ota_marker,
                        args.vpkg_test_repo is not None,
                    )
                    add_serial_to_initial_uki(staged_image)
                    boot_command = [
                        sys.executable,
                        str(pathlib.Path(__file__).with_name("test-ab-boot.py")),
                        "--image", str(staged_image),
                        "--version", version,
                        "--roothash", roothash,
                        "--ota-marker", ota_marker,
                        "--qemu", args.qemu,
                        "--accel", args.qemu_accel,
                        "--cpu", args.qemu_cpu,
                        "--memory-mib", str(args.qemu_memory_mib),
                        "--ovmf-code", str(args.ovmf_code.resolve()),
                        "--ovmf-vars", str(args.ovmf_vars.resolve()),
                        "--boot-timeout", str(args.boot_timeout),
                        "--ota-port", str(args.port),
                    ]
                else:
                    inject_bios_guest_ota_test(
                        staged_image,
                        definitions,
                        exported_key,
                        version,
                        roothash,
                        ota_marker,
                        args.vpkg_test_repo is not None,
                    )
                    boot_command = [
                        sys.executable,
                        str(pathlib.Path(__file__).with_name("test-bios-ab-boot.py")),
                        "--image", str(staged_image),
                        "--version", version,
                        "--roothash", roothash,
                        "--ota-marker", ota_marker,
                        "--qemu", args.qemu,
                        "--accel", args.qemu_accel,
                        "--cpu", args.qemu_cpu,
                        "--memory-mib", str(args.qemu_memory_mib),
                        "--boot-timeout", str(args.boot_timeout),
                        "--ota-port", str(args.port),
                    ]
                if args.log_directory:
                    boot_command.extend(["--log-directory", str(args.log_directory.resolve())])
                if args.qemu_data_dir:
                    boot_command.extend(["--qemu-data-dir", str(args.qemu_data_dir.resolve())])
                if args.vpkg_test_repo:
                    boot_command.append("--expect-vpkg-test")
                run(boot_command)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        print(
            f"Signed {args.firmware.upper()} sysupdate was installed inside the QEMU guest as Veyra {version}."
        )
        if args.firmware == "bios":
            print("SeaBIOS booted the trial, GRUB blessed it, and three failed trials returned to the prior deployment.")
        else:
            print("A deliberate three-attempt boot failure returned to the previously blessed slot.")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(str(detail), file=sys.stderr)
        print(f"test-sysupdate-image: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
