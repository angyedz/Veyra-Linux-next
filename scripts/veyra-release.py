#!/usr/bin/env python3
"""Single entry point for an EXPERIMENTAL Veyra OTA release bundle.

This creates unsigned, offline-verifiable GitHub Release assets. It does NOT
publish a release, sign metadata, install slots or declare an update trusted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from ota_chunks import MAX_ASSET_SIZE, plan, reconstruct, file_digest
from benchmark_ota import compressed_size

VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}\Z")


def sha256(path: Path) -> str:
    return file_digest(path)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def release(target: Path, output: Path, version: str, base: Path | None = None,
            base_version: str | None = None, *, threshold: float = 0.85,
            max_asset_size: int = MAX_ASSET_SIZE) -> dict:
    if not VERSION.fullmatch(version):
        raise ValueError("invalid version label")
    if base is not None and not base_version:
        raise ValueError("--base-version is required when --base is specified")
    if base is None and base_version is not None:
        raise ValueError("--base-version requires --base")
    if base_version is not None and not VERSION.fullmatch(base_version):
        raise ValueError("invalid base version")
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be within (0, 1]")
    if not 0 < max_asset_size <= MAX_ASSET_SIZE:
        raise ValueError("invalid asset limit")
    if not target.is_file() or (base is not None and not base.is_file()):
        raise ValueError("input image missing")
    if output.exists() or output.is_symlink():
        raise FileExistsError("output path already exists")
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    # Keep staging on same filesystem so final rename is atomic.
    with tempfile.TemporaryDirectory(prefix=".veyra-release-", dir=output_parent) as temp:
        stage = Path(temp)
        full = plan(target, stage / "full-work", max_asset_size=max_asset_size)
        full_manifest = stage / "ota-full.json"
        write_json(full_manifest, full)
        delta = None
        delta_bytes = None
        full_bytes = compressed_size(target)
        if base is not None:
            delta = plan(target, stage / "delta-work", base, max_asset_size=max_asset_size)
            delta_manifest = stage / "ota-delta.json"
            write_json(delta_manifest, delta)
            changed = {r["asset"]: r["compressed_size"] for r in delta["chunks"] if r["source"] == "asset"}
            delta_bytes = sum(changed.values()) + delta_manifest.stat().st_size
        mode = "delta" if delta_bytes is not None and full_bytes and delta_bytes < full_bytes * threshold else "full"

        # Every published asset is a flat file; shared content-addressed
        # chunks are deduplicated between full and delta variants.
        assets = {}
        for folder in (stage / "full-work", stage / "delta-work"):
            if not folder.exists():
                continue
            for path in folder.glob("*.zlib"):
                destination = stage / path.name
                if destination.exists():
                    if sha256(destination) != sha256(path):
                        raise ValueError("content-addressed asset collision")
                else:
                    shutil.copyfile(path, destination)
                assets[path.name] = destination

        # Verify both variants from the actual flat release directory.
        # reconstruct expects manifest and chunks alongside one another.
        verified = []
        for label, original, reference in (
            ("full", full_manifest, None),
            ("delta", stage / "ota-delta.json", base),
        ):
            if not original.exists():
                continue
            check = stage / f".verified-{label}.img"
            reconstruct(original, check, reference)
            if sha256(check) != full["target_sha256"]:
                raise ValueError("release reconstruction mismatch")
            check.unlink()
            verified.append(label)
        shutil.rmtree(stage / "full-work")
        if (stage / "delta-work").exists():
            shutil.rmtree(stage / "delta-work")
        files = {}
        for path in sorted(stage.iterdir()):
            if path.is_file():
                if path.stat().st_size > max_asset_size:
                    raise ValueError(f"GitHub asset too large: {path.name}")
                files[path.name] = {"sha256": sha256(path), "size": path.stat().st_size}
        metadata = {
            "format": "veyra-release-bundle-v1",
            "status": "UNSIGNED-EXPERIMENTAL-NOT-INSTALLABLE",
            "version": version,
            "base_version": base_version,
            "target_sha256": full["target_sha256"],
            "base_sha256": sha256(base) if base else None,
            "preferred_transport": mode,
            "delta_transfer_bytes": delta_bytes,
            "full_compressed_estimate_bytes": full_bytes,
            "verified_variants": verified,
            "files": files,
        }
        write_json(stage / "release.json", metadata)
        if (stage / "release.json").stat().st_size > max_asset_size:
            raise ValueError("release manifest too large")
        # Preserve final directory even after temporary context cleanup.
        os.rename(stage, output)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--base-version")
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--asset-limit", type=int, default=MAX_ASSET_SIZE)
    args = parser.parse_args()
    result = release(args.target, args.output, args.version, args.base,
                     args.base_version, threshold=args.threshold,
                     max_asset_size=args.asset_limit)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
