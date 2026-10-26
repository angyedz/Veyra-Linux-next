#!/usr/bin/env python3
"""Prototype content-addressed OTA chunk planner. NOT a production updater.

Produces deterministic JSON manifests and bounded, compressed changed chunks.
No installed partition is written and no release is trusted by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import zlib

CHUNK_SIZE = 4 * 1024 * 1024
MAX_ASSET_SIZE = 2_000_000_000  # conservative below GitHub's 2 GiB per-file cap
# Each asset contains one independently compressed CHUNK_SIZE block. Leave
# generous room for zlib framing while keeping corrupted manifests bounded.
MAX_COMPRESSED_CHUNK_SIZE = CHUNK_SIZE + 64 * 1024
FORMAT = "veyra-ota-chunks-v1"
PACKED_FORMAT = "veyra-ota-chunks-v2"
PACK_TARGET_SIZE = 256 * 1024 * 1024
MAX_PACK_SIZE = 257 * 1024 * 1024
MAX_PACK_MEMBERS = 128
CHUNK_ASSET = re.compile(r"[0-9a-f]{64}\.zlib\Z")
PACK_ASSET = re.compile(r"chunks-([0-9a-f]{64})\.tar\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunks(path: Path):
    with path.open("rb") as source:
        while data := source.read(CHUNK_SIZE):
            yield data


def plan(target: Path, output: Path, base: Path | None = None, *,
         max_asset_size: int = MAX_ASSET_SIZE) -> dict:
    if not 0 < max_asset_size <= MAX_ASSET_SIZE:
        raise ValueError("asset limit must be positive and at most 2,000,000,000 bytes")
    output.mkdir(parents=True, exist_ok=True)
    base_digests = [digest(part) for part in chunks(base)] if base else []
    records = []
    stored = {}
    target_hash = hashlib.sha256()
    for index, part in enumerate(chunks(target)):
        target_hash.update(part)
        sha = digest(part)
        reused = index < len(base_digests) and base_digests[index] == sha
        record = {"index": index, "sha256": sha, "size": len(part), "source": "base" if reused else "asset"}
        if not reused:
            compressed = zlib.compress(part, level=9)
            if len(compressed) > min(max_asset_size, MAX_COMPRESSED_CHUNK_SIZE):
                raise ValueError(f"compressed chunk {index} exceeds asset limit")
            name = f"{sha}.zlib"
            record["asset"] = name
            record["compressed_size"] = len(compressed)
            if name not in stored:
                (output / name).write_bytes(compressed)
                stored[name] = len(compressed)
        records.append(record)
    manifest = {
        "format": FORMAT,
        "chunk_size": CHUNK_SIZE,
        "target_size": target.stat().st_size,
        "target_sha256": target_hash.hexdigest(),
        "base_sha256": file_digest(base) if base else None,
        "chunks": records,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return manifest


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(CHUNK_SIZE):
            h.update(data)
    return h.hexdigest()


def validate_manifest(m: dict) -> None:
    """Reject malformed or oversized manifests before touching the output."""
    import re

    if (not isinstance(m, dict) or m.get("format") not in {FORMAT, PACKED_FORMAT}
            or m.get("chunk_size") != CHUNK_SIZE):
        raise ValueError("unsupported manifest")
    packed = m["format"] == PACKED_FORMAT
    size = m.get("target_size")
    if type(size) is not int or size < 0:
        raise ValueError("invalid target size")
    expected = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
    records = m.get("chunks")
    if not isinstance(records, list) or len(records) != expected:
        raise ValueError("invalid chunk count")
    sha_pattern = re.compile(r"[0-9a-f]{64}\Z")
    if not isinstance(m.get("target_sha256"), str) or not sha_pattern.fullmatch(m["target_sha256"]):
        raise ValueError("invalid target digest")
    base_sha = m.get("base_sha256")
    if base_sha is not None and (not isinstance(base_sha, str) or not sha_pattern.fullmatch(base_sha)):
        raise ValueError("invalid base digest")
    pack_sizes: dict[str, tuple[str, int]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or type(record.get("index")) is not int or record["index"] != index:
            raise ValueError("invalid chunk index")
        part_size = min(CHUNK_SIZE, size - index * CHUNK_SIZE)
        if type(record.get("size")) is not int or record["size"] != part_size:
            raise ValueError("invalid chunk size")
        sha = record.get("sha256")
        if not isinstance(sha, str) or not sha_pattern.fullmatch(sha):
            raise ValueError("invalid chunk digest")
        source = record.get("source")
        if source == "base":
            if base_sha is None or any(
                key in record for key in ("asset", "compressed_size", "pack", "pack_sha256", "pack_size", "member")
            ):
                raise ValueError("invalid base chunk")
        elif source == "asset":
            if record.get("asset") != sha + ".zlib":
                raise ValueError("unsafe or mismatched asset name")
            compressed_size = record.get("compressed_size")
            if type(compressed_size) is not int or not 0 < compressed_size <= MAX_COMPRESSED_CHUNK_SIZE:
                raise ValueError("invalid compressed asset size")
            pack_keys = ("pack", "pack_sha256", "pack_size", "member")
            if packed:
                pack = record.get("pack")
                pack_sha256 = record.get("pack_sha256")
                pack_size = record.get("pack_size")
                member = record.get("member")
                match = PACK_ASSET.fullmatch(pack) if isinstance(pack, str) else None
                if (match is None or not isinstance(pack_sha256, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", pack_sha256)
                        or match.group(1) != pack_sha256
                        or type(pack_size) is not int or not 0 < pack_size <= MAX_PACK_SIZE
                        or member != record["asset"]):
                    raise ValueError("invalid packed asset reference")
                metadata = (pack_sha256, pack_size)
                if pack in pack_sizes and pack_sizes[pack] != metadata:
                    raise ValueError("conflicting packed asset metadata")
                pack_sizes[pack] = metadata
            elif any(key in record for key in pack_keys):
                raise ValueError("unexpected packed asset metadata")
        else:
            raise ValueError("unknown chunk source")


def decompress_stream(source, compressed_size: int, expected_size: int) -> bytes:
    """Read one bounded zlib chunk from a binary stream."""
    if not 0 < compressed_size <= MAX_COMPRESSED_CHUNK_SIZE:
        raise ValueError("invalid compressed asset size")
    if not 0 < expected_size <= CHUNK_SIZE:
        raise ValueError("invalid decompressed chunk size")

    decompressor = zlib.decompressobj()
    output = bytearray()
    total = 0
    while block := source.read(min(64 * 1024, compressed_size - total + 1)):
        total += len(block)
        if total > compressed_size:
            raise ValueError("compressed asset exceeds declared size")
        pending = block
        while pending:
            # max_length caps output; unconsumed_tail is fed on the next pass.
            remaining = expected_size + 1 - len(output)
            part = decompressor.decompress(pending, remaining)
            output.extend(part)
            if len(output) > expected_size:
                raise ValueError("decompressed chunk exceeds declared size")
            if decompressor.unused_data:
                raise ValueError("invalid compressed asset")
            unconsumed = decompressor.unconsumed_tail
            if unconsumed and len(unconsumed) == len(pending) and not part:
                raise ValueError("invalid compressed asset")
            pending = unconsumed
    if total != compressed_size:
        raise ValueError("compressed asset size mismatch")
    if not decompressor.eof or decompressor.unconsumed_tail or decompressor.unused_data:
        raise ValueError("invalid compressed asset")
    if len(output) != expected_size:
        raise ValueError("decompressed chunk size mismatch")
    return bytes(output)


def decompress_asset(path: Path, compressed_size: int, expected_size: int) -> bytes:
    """Read one bounded zlib chunk without loading its compressed asset at once."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != compressed_size:
            raise ValueError("compressed asset size mismatch")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            return decompress_stream(source, compressed_size, expected_size)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def open_verified_pack(path: Path, record: dict):
    """Open a bounded tar pack and validate all members without extracting paths."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    source = None
    archive = None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != record["pack_size"]:
            raise ValueError("packed asset size mismatch")
        source = os.fdopen(descriptor, "rb")
        descriptor = -1
        hasher = hashlib.sha256()
        while block := source.read(1024 * 1024):
            hasher.update(block)
        if hasher.hexdigest() != record["pack_sha256"]:
            raise ValueError("packed asset digest mismatch")
        source.seek(0)
        archive = tarfile.open(fileobj=source, mode="r:")
        members = {}
        for member in archive:
            if len(members) >= MAX_PACK_MEMBERS:
                raise ValueError("packed asset contains too many members")
            if (not member.isfile() or not CHUNK_ASSET.fullmatch(member.name)
                    or member.size <= 0 or member.size > MAX_COMPRESSED_CHUNK_SIZE
                    or member.name in members):
                raise ValueError("invalid packed asset member")
            members[member.name] = member
        if record["member"] not in members:
            raise ValueError("packed asset member is missing")
        if members[record["member"]].size != record["compressed_size"]:
            raise ValueError("packed asset member size mismatch")
        return source, archive, members
    except BaseException:
        if archive is not None:
            archive.close()
        if source is not None:
            source.close()
        if descriptor >= 0:
            os.close(descriptor)
        raise


def decompress_packed_asset(archive, members: dict, record: dict) -> bytes:
    member = members.get(record["member"])
    if member is None or member.size != record["compressed_size"]:
        raise ValueError("packed asset member size mismatch")
    source = archive.extractfile(member)
    if source is None:
        raise ValueError("packed asset member is unreadable")
    with source:
        return decompress_stream(source, record["compressed_size"], record["size"])


def reconstruct(manifest_path: Path, output: Path, base: Path | None = None) -> None:
    """Offline verification harness; never write to an installed system slot."""
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(m)
    if m["base_sha256"] is not None and (base is None or file_digest(base) != m["base_sha256"]):
        raise ValueError("base image hash mismatch")
    if output.exists():
        raise FileExistsError("refusing to overwrite existing output")
    open_packs = {}
    try:
        with output.open("xb") as dst:
            with base.open("rb") if base else open("/dev/null", "rb") as src:
                for index, record in enumerate(m["chunks"]):
                    if record["index"] != index:
                        raise ValueError("nonsequential chunk index")
                    if record["source"] == "base":
                        src.seek(index * CHUNK_SIZE)
                        part = src.read(record["size"])
                    elif record["source"] == "asset":
                        name = record["asset"]
                        if name != record["sha256"] + ".zlib":
                            raise ValueError("unsafe or mismatched asset name")
                        if m["format"] == PACKED_FORMAT:
                            pack_name = record["pack"]
                            if pack_name not in open_packs:
                                open_packs[pack_name] = open_verified_pack(
                                    manifest_path.parent / pack_name, record
                                )
                            _, archive, members = open_packs[pack_name]
                            part = decompress_packed_asset(archive, members, record)
                        else:
                            part = decompress_asset(
                                manifest_path.parent / name,
                                record["compressed_size"],
                                record["size"],
                            )
                    else:
                        raise ValueError("unknown chunk source")
                    if len(part) != record["size"] or digest(part) != record["sha256"]:
                        raise ValueError("chunk integrity mismatch")
                    dst.write(part)
        if output.stat().st_size != m["target_size"] or file_digest(output) != m["target_sha256"]:
            raise ValueError("target image hash mismatch")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        for source, archive, _ in open_packs.values():
            archive.close()
            source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--asset-limit", type=int, default=MAX_ASSET_SIZE)
    args = parser.parse_args()
    manifest = plan(args.target, args.output_dir, args.base, max_asset_size=args.asset_limit)
    changed = sum(r["compressed_size"] for r in manifest["chunks"] if r["source"] == "asset")
    print(f"Planned {len(manifest['chunks'])} chunks; compressed changed bytes: {changed}")


if __name__ == "__main__":
    main()
