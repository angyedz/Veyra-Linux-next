#!/usr/bin/env python3
"""Offline OTA size benchmark: compare chunked delta with full compressed image.

Does not download, publish, sign, or install any update.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zlib

from ota_chunks import CHUNK_SIZE, plan


def compressed_size(path: Path) -> int:
    compressor = zlib.compressobj(level=9)
    total = 0
    with path.open("rb") as stream:
        while data := stream.read(CHUNK_SIZE):
            total += len(compressor.compress(data))
    total += len(compressor.flush())
    return total


def measure(base: Path, target: Path, bundle: Path, *, threshold: float = 0.85) -> dict:
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be within (0, 1]")
    manifest = plan(target, bundle, base)
    changed_assets = {
        record["asset"]: record["compressed_size"]
        for record in manifest["chunks"] if record["source"] == "asset"
    }
    delta_bytes = sum(changed_assets.values()) + (bundle / "manifest.json").stat().st_size
    full_bytes = compressed_size(target)
    result = {
        "base": str(base),
        "target": str(target),
        "target_uncompressed_bytes": target.stat().st_size,
        "full_compressed_bytes": full_bytes,
        "delta_transfer_bytes": delta_bytes,
        "reused_chunks": sum(r["source"] == "base" for r in manifest["chunks"]),
        "changed_chunks": sum(r["source"] == "asset" for r in manifest["chunks"]),
        "delta_ratio_to_full": round(delta_bytes / full_bytes, 4) if full_bytes else None,
        "recommendation": "delta" if full_bytes and delta_bytes < full_bytes * threshold else "full",
        "threshold": threshold,
    }
    (bundle / "benchmark.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--threshold", type=float, default=0.85)
    args = parser.parse_args()
    print(json.dumps(measure(args.base, args.target, args.bundle, threshold=args.threshold),
                     sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
