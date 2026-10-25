#!/usr/bin/env python3
"""Exercise a signed sysupdate A-to-B slot write against a disposable disk image."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import pathlib
import re
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


def write_compressed(source: pathlib.Path, destination: pathlib.Path) -> None:
    run(["zstd", "-q", "-T1", "-f", str(source), "-o", str(destination)])


def write_manifest(source: pathlib.Path, assets: list[str], fingerprint: str) -> None:
    lines = []
    for name in sorted(assets):
        digest = hashlib.sha256((source / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    manifest = source / "SHA256SUMS"
    manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
    run([
        "gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
        "--local-user", fingerprint,
        "--detach-sign", "--output", str(source / "SHA256SUMS.gpg"), str(manifest),
    ])


def write_test_definitions(
    source: pathlib.Path, definitions: pathlib.Path, base_url: str
) -> pathlib.Path:
    destination = source / "definitions"
    destination.mkdir()
    expected = {
        "10-root-verity.transfer",
        "20-root.transfer",
        "99-uki.transfer",
    }
    actual = {path.name for path in definitions.glob("*.transfer")}
    if actual != expected:
        raise RuntimeError(f"Unexpected system update transfer set: {sorted(actual)}")
    for name in sorted(expected):
        content = (definitions / name).read_text(encoding="utf-8")
        content, replacements = re.subn(
            r"(?m)^Path=https://github\.com/angyedz/Veyra-next/releases/latest/download/?[ \t]*$",
            f"Path={base_url}",
            content,
        )
        if replacements != 1:
            raise RuntimeError(f"Could not redirect the release source in {name}.")
        (destination / name).write_text(content, encoding="utf-8")
    return destination


def inspect_partitions(image: pathlib.Path) -> tuple[list[dict[str, object]], int]:
    layout = run(["sfdisk", "--json", str(image)], stdout=subprocess.PIPE).stdout
    import json

    partition_table = json.loads(layout)["partitiontable"]
    return partition_table["partitions"], int(partition_table.get("sectorsize", 512))


def require_partition_payload(
    image: pathlib.Path, partitions: list[dict[str, object]], sector_size: int,
    label: str, payload: bytes, expected_uuid: str,
) -> None:
    matches = [partition for partition in partitions if partition.get("name") == label]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one staged partition named {label}, found {len(matches)}.")
    partition = matches[0]
    actual_uuid = str(partition.get("uuid", "")).replace("-", "").lower()
    if actual_uuid != expected_uuid.replace("-", "").lower():
        raise RuntimeError(f"Staged partition {label} has the wrong UUID.")
    with image.open("rb") as disk:
        disk.seek(int(partition["start"]) * sector_size)
        if disk.read(len(payload)) != payload:
            raise RuntimeError(f"Staged partition {label} does not contain the downloaded payload.")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--transfer-dir", required=True, type=pathlib.Path)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sudo-sysupdate", action="store_true")
    args = parser.parse_args()

    try:
        version = next_version(args.current_version)
        image = args.image.resolve()
        if not image.is_file():
            raise RuntimeError(f"Missing test image: {image}")
        sysupdate = pathlib.Path("/usr/lib/systemd/systemd-sysupdate")
        if not sysupdate.is_file():
            raise RuntimeError(f"Missing systemd-sysupdate binary: {sysupdate}")

        with tempfile.TemporaryDirectory(prefix="veyra-sysupdate-test-") as temporary:
            source = pathlib.Path(temporary)
            root_uuid = str(uuid.uuid4())
            verity_uuid = str(uuid.uuid4())
            root_payload = (f"VEYRA-CI-ROOT-{version}\n".encode("ascii")).ljust(4096, b"\0")
            verity_payload = (f"VEYRA-CI-VERITY-{version}\n".encode("ascii")).ljust(4096, b"\0")
            root_raw = source / "root.raw"
            verity_raw = source / "verity.raw"
            root_raw.write_bytes(root_payload)
            verity_raw.write_bytes(verity_payload)

            root_asset = f"veyra-next_{version}_{root_uuid}.root.raw.zst"
            verity_asset = f"veyra-next_{version}_{verity_uuid}.root-verity.raw.zst"
            uki_asset = f"veyra-next_{version}.efi"
            write_compressed(root_raw, source / root_asset)
            write_compressed(verity_raw, source / verity_asset)
            # The staging test validates transfer and boot-count naming. It deliberately
            # does not boot this placeholder UKI or make a rollback claim.
            (source / uki_asset).write_bytes(f"Veyra CI non-bootable UKI placeholder {version}\n".encode())
            write_manifest(source, [root_asset, verity_asset, uki_asset], args.fingerprint)

            handler = functools.partial(QuietHandler, directory=str(source))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                definitions = write_test_definitions(
                    source, args.transfer_dir, f"http://127.0.0.1:{args.port}/"
                )
                command = [
                    str(sysupdate), f"--definitions={definitions}", f"--image={image}",
                    "--verify=yes", "--no-pager", "update", version,
                ]
                if args.sudo_sysupdate:
                    command.insert(0, "sudo")
                run(command)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            partitions, sector_size = inspect_partitions(image)
            require_partition_payload(
                image, partitions, sector_size, f"veyra-next_{version}", root_payload, root_uuid
            )
            require_partition_payload(
                image, partitions, sector_size, f"veyra-next_{version}_verity", verity_payload, verity_uuid
            )
            esp_type = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
            esp_matches = [p for p in partitions if str(p.get("type", "")).lower() == esp_type]
            if len(esp_matches) != 1:
                raise RuntimeError("Expected one EFI system partition after update staging.")
            esp_offset = int(esp_matches[0]["start"]) * sector_size
            listing = run([
                "mdir", "-i", f"{image}@@{esp_offset}", "::/EFI/Linux",
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
            trial_name = f"veyra-next_{version}+3-0.efi"
            if trial_name.lower() not in listing.lower():
                raise RuntimeError(f"The trial UKI with three boot attempts is missing: {trial_name}")

        print(f"Signed sysupdate staged Veyra {version} into the inactive root and verity slots.")
        print("The CI UKI placeholder is intentionally not booted; failed-boot fallback still requires VM validation.")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(str(detail), file=sys.stderr)
        print(f"test-sysupdate-image: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
