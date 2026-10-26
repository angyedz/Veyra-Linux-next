#!/usr/bin/env python3
"""Untrusted OTA asset downloader for offline experiments; no slot installation.

Requires an independently authenticated manifest before production use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from ota_chunks import CHUNK_SIZE, MAX_COMPRESSED_CHUNK_SIZE, validate_manifest

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BUFFER = 1024 * 1024


def asset_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(BUFFER):
            h.update(data)
    return h.hexdigest()


def required_assets(manifest: dict) -> dict[str, int]:
    validate_manifest(manifest)
    result = {}
    for record in manifest["chunks"]:
        if record["source"] == "asset":
            name = record["asset"]
            size = record["compressed_size"]
            if name in result and result[name] != size:
                raise ValueError("conflicting size for repeated asset")
            result[name] = size
    return result


def fetch_asset(base_url: str, cache: Path, name: str, size: int) -> str:
    """Download/resume a bounded asset into a private content-addressed cache.

    Returns 'cached' or 'downloaded'. The caller must authenticate release
    metadata; hashes alone cannot establish publisher authenticity.
    """
    if not SHA256.fullmatch(name.removesuffix(".zlib")) or not name.endswith(".zlib"):
        raise ValueError("invalid asset name")
    if not 0 < size <= MAX_COMPRESSED_CHUNK_SIZE:
        raise ValueError("invalid asset size")
    from urllib.parse import urlsplit
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("clean HTTPS base URL required")
    if cache.is_symlink():
        raise ValueError("symlink cache directory forbidden")
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / name
    partial = cache / (name + ".part")
    if target.is_symlink() or partial.is_symlink():
        raise ValueError("symlink asset forbidden")
    if target.exists():
        if target.stat().st_size == size and compressed_digest(target, name):
            return "cached"
        target.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    # A complete .part may be left by a crash between fsync and rename.
    # Verify and promote it without another network request.
    if offset == size and compressed_digest(partial, name):
        os.replace(partial, target)
        return "cached"
    if offset == size:
        partial.unlink()
        offset = 0
    url = base_url.rstrip("/") + "/" + name
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        if not response.geturl().startswith('https://'):
            raise ValueError('HTTPS redirect downgrade forbidden')
        status = response.status
        if offset and status == 206:
            expected = f"bytes {offset}-{size - 1}/{size}"
            if response.headers.get("Content-Range") != expected:
                raise ValueError("unexpected Content-Range")
            mode = "ab"
        elif status == 200:
            offset = 0
            mode = "wb"
        else:
            raise ValueError(f"unexpected HTTP status {status}")
        downloaded = offset
        with partial.open(mode) as dst:
            while block := response.read(min(BUFFER, size - downloaded + 1)):
                downloaded += len(block)
                if downloaded > size:
                    raise ValueError("asset exceeds declared size")
                dst.write(block)
    if partial.stat().st_size != size or not compressed_digest(partial, name):
        partial.unlink(missing_ok=True)
        raise ValueError("downloaded asset failed integrity check")
    os.replace(partial, target)
    return "downloaded"


def compressed_digest(path: Path, name: str) -> bool:
    # Filename names the SHA-256 of the *uncompressed* chunk, so validation
    # requires bounded decompression rather than hashing compressed bytes.
    import zlib

    expected = name[:-5]
    h = hashlib.sha256()
    d = zlib.decompressobj()
    total = 0
    try:
        with path.open("rb") as f:
            while blob := f.read(BUFFER):
                data = d.decompress(blob, CHUNK_SIZE - total + 1)
                total += len(data)
                if total > CHUNK_SIZE or d.unconsumed_tail:
                    return False
                h.update(data)
        return d.eof and not d.unused_data and h.hexdigest() == expected
    except (OSError, zlib.error):
        return False


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("asset_base_url")
    p.add_argument("cache", type=Path)
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    for name, size in required_assets(manifest).items():
        print(f"{name}: {fetch_asset(args.asset_base_url, args.cache, name, size)}")


if __name__ == "__main__":
    main()
