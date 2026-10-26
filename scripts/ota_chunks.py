#!/usr/bin/env python3
"""Prototype content-addressed OTA chunk planner. NOT a production updater.

Produces deterministic JSON manifests and bounded, compressed changed chunks.
No installed partition is written and no release is trusted by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zlib

CHUNK_SIZE = 4 * 1024 * 1024
MAX_ASSET_SIZE = 2_000_000_000  # conservative below GitHub's 2 GiB per-file cap
FORMAT = "veyra-ota-chunks-v1"


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
            if len(compressed) > max_asset_size:
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

    if not isinstance(m, dict) or m.get("format") != FORMAT or m.get("chunk_size") != CHUNK_SIZE:
        raise ValueError("unsupported manifest")
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
            if base_sha is None or "asset" in record or "compressed_size" in record:
                raise ValueError("invalid base chunk")
        elif source == "asset":
            if record.get("asset") != sha + ".zlib":
                raise ValueError("unsafe or mismatched asset name")
            compressed_size = record.get("compressed_size")
            if type(compressed_size) is not int or not 0 < compressed_size <= MAX_ASSET_SIZE:
                raise ValueError("invalid compressed asset size")
        else:
            raise ValueError("unknown chunk source")


def reconstruct(manifest_path: Path, output: Path, base: Path | None = None) -> None:
    """Offline verification harness; never write to an installed system slot."""
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(m)
    if m["base_sha256"] is not None and (base is None or file_digest(base) != m["base_sha256"]):
        raise ValueError("base image hash mismatch")
    if output.exists():
        raise FileExistsError("refusing to overwrite existing output")
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
                        blob = (manifest_path.parent / name).read_bytes()
                        if len(blob) != record["compressed_size"]:
                            raise ValueError("compressed asset size mismatch")
                        decompressor = zlib.decompressobj()
                        part = decompressor.decompress(blob, CHUNK_SIZE + 1)
                        if decompressor.unconsumed_tail or not decompressor.eof or decompressor.unused_data:
                            raise ValueError("invalid compressed asset")
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
