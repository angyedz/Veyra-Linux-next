#!/usr/bin/env python3
"""Download and verify the RPMs from the currently published Veyra channels."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


CHANNELS = ("main", "testing")
REPO_NS = "{http://linux.duke.edu/metadata/repo}"
COMMON_NS = "{http://linux.duke.edu/metadata/common}"


class MirrorError(RuntimeError):
    pass


class NotPublished(MirrorError):
    pass


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Veyra-package-publisher/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotPublished(f"No published RPM repository at {url}") from error
        raise MirrorError(f"Could not download {url}: HTTP {error.code}") from error
    except OSError as error:
        raise MirrorError(f"Could not download {url}: {error}") from error


def parse_primary_location(repomd: bytes) -> tuple[str, str, str]:
    try:
        root = ET.fromstring(repomd)
    except ET.ParseError as error:
        raise MirrorError("The published repomd.xml is malformed.") from error
    for record in root.findall(f"{REPO_NS}data"):
        if record.get("type") != "primary":
            continue
        location = record.find(f"{REPO_NS}location")
        checksum = record.find(f"{REPO_NS}checksum")
        if location is None or checksum is None or not location.get("href"):
            raise MirrorError("The primary metadata record is missing its path or checksum.")
        algorithm = checksum.get("type", "").lower()
        digest = (checksum.text or "").lower()
        if algorithm not in {"sha256", "sha512"} or not digest:
            raise MirrorError(f"Unsupported primary metadata checksum algorithm: {algorithm!r}")
        href = location.get("href", "")
        safe_relative_href(href)
        return href, algorithm, digest
    raise MirrorError("The published repomd.xml has no primary metadata record.")


def safe_relative_href(href: str) -> str:
    path = PurePosixPath(href)
    if (
        not href
        or path.is_absolute()
        or href != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MirrorError(f"Refusing unsafe repository path: {href!r}")
    if "\\" in href or "%" in href:
        raise MirrorError(f"Refusing ambiguous repository path: {href!r}")
    return href


def parse_primary_metadata(primary: bytes) -> list[tuple[str, str, str, str]]:
    try:
        root = ET.fromstring(primary)
    except ET.ParseError as error:
        raise MirrorError("The published primary.xml is malformed.") from error
    assets: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for package in root.findall(f"{COMMON_NS}package"):
        if package.get("type") != "rpm":
            continue
        name_node = package.find(f"{COMMON_NS}name")
        location = package.find(f"{COMMON_NS}location")
        checksum = package.find(f"{COMMON_NS}checksum")
        if name_node is None or location is None or checksum is None:
            raise MirrorError("The published primary.xml contains an incomplete RPM record.")
        name = name_node.text or ""
        href = safe_relative_href(location.get("href", ""))
        checksum_type = checksum.get("type", "").lower()
        checksum_value = (checksum.text or "").lower()
        if not name.startswith("veyra-"):
            raise MirrorError(f"Refusing non-Veyra package in published channel: {name!r}")
        if not href.endswith(".rpm") or checksum_type not in {"sha256", "sha512"}:
            raise MirrorError(f"Refusing unsupported package record: {href!r}")
        if not checksum_value or href in seen:
            raise MirrorError(f"Invalid or duplicated RPM record: {href!r}")
        seen.add(href)
        assets.append((name, href, checksum_type, checksum_value))
    return assets


def verify_signature(signature: bytes, data: bytes, directory: Path) -> None:
    signature_path = directory / "repomd.xml.asc"
    metadata_path = directory / "repomd.xml"
    signature_path.write_bytes(signature)
    metadata_path.write_bytes(data)
    try:
        subprocess.run(
            ["gpg", "--batch", "--verify", str(signature_path), str(metadata_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise MirrorError("gpg is required to verify the published repository metadata.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "signature verification failed").strip()
        raise MirrorError(f"Published repository metadata signature is invalid: {detail}") from error


def list_primary_key_fingerprints() -> set[str]:
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--with-colons", "--list-keys"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
    except FileNotFoundError as error:
        raise MirrorError("gpg is required to inspect the published repository key.") from error
    except subprocess.CalledProcessError as error:
        raise MirrorError("Could not list the imported RPM repository signing keys.") from error

    fingerprints: set[str] = set()
    expect_primary_fingerprint = False
    for line in result.splitlines():
        record = line.split(":")
        if record[0] in {"pub", "sec"}:
            expect_primary_fingerprint = True
        elif record[0] in {"sub", "ssb"}:
            expect_primary_fingerprint = False
        elif record[0] == "fpr" and expect_primary_fingerprint and len(record) > 9:
            fingerprints.add(record[9].upper())
            expect_primary_fingerprint = False
    return fingerprints


def rpm_signatures_are_valid(output: str) -> bool:
    signature_lines = [line for line in output.splitlines() if "signature" in line.lower()]
    return bool(signature_lines) and all(
        re.search(r":[ \t]*OK[ \t]*$", line, re.IGNORECASE) is not None
        for line in signature_lines
    )


def download_channel(base_url: str, channel: str, site: Path) -> int | None:
    repository_url = f"{base_url.rstrip('/')}/packages/{channel}/x86_64/"
    try:
        repomd = fetch(repository_url + "repodata/repomd.xml")
    except NotPublished:
        return None
    try:
        signature = fetch(repository_url + "repodata/repomd.xml.asc")
    except NotPublished as error:
        raise MirrorError(f"Published {channel} repository has no repomd.xml signature.") from error

    with tempfile.TemporaryDirectory(prefix="veyra-repomd-") as temporary:
        verify_signature(signature, repomd, Path(temporary))

    primary_href, algorithm, expected_digest = parse_primary_location(repomd)
    primary_data = fetch(repository_url + primary_href)
    actual_digest = hashlib.new(algorithm, primary_data).hexdigest()
    if actual_digest != expected_digest:
        raise MirrorError(f"Primary metadata checksum does not match for {channel}.")
    if primary_href.endswith(".gz"):
        try:
            primary_xml = gzip.decompress(primary_data)
        except OSError as error:
            raise MirrorError(f"Cannot decompress primary metadata for {channel}.") from error
    elif primary_href.endswith(".zst"):
        try:
            primary_xml = subprocess.run(
                ["zstd", "-q", "-d", "-c"],
                input=primary_data,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise MirrorError(f"Cannot decompress primary metadata for {channel}.") from error
    else:
        primary_xml = primary_data

    repository_directory = site / "packages" / channel / "x86_64"
    repository_directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, href, checksum_type, checksum_value in parse_primary_metadata(primary_xml):
        url = repository_url + href
        payload = fetch(url)
        if hashlib.new(checksum_type, payload).hexdigest() != checksum_value:
            raise MirrorError(f"RPM checksum does not match for {name} at {href}.")
        target = repository_directory / PurePosixPath(href).name
        if target.exists() and target.read_bytes() != payload:
            raise MirrorError(f"Conflicting RPM filename while mirroring: {target.name}")
        target.write_bytes(payload)
        try:
            package_name = subprocess.run(
                ["rpm", "-qp", "--qf", "%{NAME}", str(target)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            signature_check = subprocess.run(
                ["rpm", "--checksig", "--verbose", str(target)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ).stdout
        except FileNotFoundError as error:
            raise MirrorError("rpm is required to validate mirrored package files.") from error
        except subprocess.CalledProcessError as error:
            raise MirrorError(f"Mirrored RPM could not be verified: {target.name}") from error
        if package_name != name:
            raise MirrorError(f"RPM name does not match primary metadata for {target.name}.")
        if not rpm_signatures_are_valid(signature_check):
            raise MirrorError(f"Mirrored RPM has no valid signature: {target.name}")
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--expected-fingerprint", required=True)
    args = parser.parse_args()
    args.site.mkdir(parents=True, exist_ok=True)

    try:
        try:
            old_key = fetch(args.base_url.rstrip("/") + "/RPM-GPG-KEY-VEYRA")
        except NotPublished:
            old_key = b""
        if old_key:
            with tempfile.TemporaryDirectory(prefix="veyra-old-key-") as temporary:
                old_key_path = Path(temporary) / "RPM-GPG-KEY-VEYRA"
                old_key_path.write_bytes(old_key)
                subprocess.run(["gpg", "--batch", "--import", str(old_key_path)], check=True)
            expected = args.expected_fingerprint.replace(" ", "").upper()
            if not re.fullmatch(r"[0-9A-F]{40,64}", expected):
                raise MirrorError("The expected RPM key fingerprint is malformed.")
            fingerprints = list_primary_key_fingerprints()
            if fingerprints != {expected}:
                raise MirrorError(
                    "Published RPM repository key fingerprint does not match the configured "
                    f"signing key ({', '.join(sorted(fingerprints)) or 'no key found'}). "
                    "Refusing unverified key changes."
                )
            subprocess.run(["rpm", "--import", str(old_key_path)], check=True)
        else:
            expected = args.expected_fingerprint.replace(" ", "").upper()
            if not re.fullmatch(r"[0-9A-F]{40,64}", expected):
                raise MirrorError("The expected RPM key fingerprint is malformed.")
            fingerprints = list_primary_key_fingerprints()
            if fingerprints != {expected}:
                raise MirrorError("The RPM signing key imported by CI does not match its fingerprint.")

        total = 0
        published_channels = 0
        for channel in CHANNELS:
            count = download_channel(args.base_url, channel, args.site)
            if count is not None:
                published_channels += 1
                total += count
            if old_key and count is None:
                raise MirrorError(
                    f"Published site has a key but its {channel} repository is missing."
                )
            print(f"Mirrored {count or 0} RPM(s) from {channel}.")
        if published_channels and not old_key:
            raise MirrorError("Published RPM repositories exist but their public key is missing.")
    except (MirrorError, OSError, subprocess.CalledProcessError) as error:
        print(f"mirror-package-site: {error}", file=sys.stderr)
        return 1
    print(f"Mirrored {total} previously published Veyra RPM(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
