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
import tarfile
import tempfile

from ota_chunks import (
    MAX_ASSET_SIZE,
    MAX_COMPRESSED_CHUNK_SIZE,
    MAX_PACK_MEMBERS,
    MAX_PACK_SIZE,
    PACKED_FORMAT,
    PACK_TARGET_SIZE,
    plan,
    reconstruct,
    file_digest,
)
from benchmark_ota import compressed_size

VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}\Z")
CHUNK_ASSET = re.compile(r"[0-9a-f]{64}\.zlib\Z")
MAX_GITHUB_RELEASE_ASSETS = 1000


def sha256(path: Path) -> str:
    return file_digest(path)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def pack_assets(stage: Path, assets: dict[str, Path], *, max_asset_size: int,
                pack_target_size: int) -> dict[str, dict]:
    """Pack unique chunk files into deterministic, bounded GitHub assets."""
    if type(pack_target_size) is not int or pack_target_size <= 0:
        raise ValueError("invalid pack target size")
    effective_target = min(pack_target_size, max_asset_size, MAX_PACK_SIZE)
    groups: list[list[tuple[str, Path, int]]] = []
    current: list[tuple[str, Path, int]] = []
    current_size = 0
    for name, path in sorted(assets.items()):
        if not CHUNK_ASSET.fullmatch(name):
            raise ValueError("unsafe chunk asset name")
        size = path.stat().st_size
        if not 0 < size <= min(MAX_COMPRESSED_CHUNK_SIZE, max_asset_size):
            raise ValueError("compressed chunk asset exceeds its size limit")
        if current and (current_size + size > effective_target or len(current) >= MAX_PACK_MEMBERS):
            groups.append(current)
            current = []
            current_size = 0
        current.append((name, path, size))
        current_size += size
    if current:
        groups.append(current)

    references: dict[str, dict] = {}
    for index, group in enumerate(groups):
        temporary_pack = stage / f".chunks-{index:04d}.tar"
        with tarfile.open(temporary_pack, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, path, size in group:
                info = tarfile.TarInfo(name)
                info.size = size
                info.mode = 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as source:
                    archive.addfile(info, source)
        pack_size = temporary_pack.stat().st_size
        if not 0 < pack_size <= min(max_asset_size, MAX_PACK_SIZE):
            raise ValueError("packed chunk asset exceeds its size limit")
        pack_sha256 = sha256(temporary_pack)
        pack_name = f"chunks-{pack_sha256}.tar"
        destination = stage / pack_name
        if destination.exists():
            if sha256(destination) != pack_sha256:
                raise ValueError("content-addressed pack collision")
            temporary_pack.unlink()
        else:
            os.replace(temporary_pack, destination)
        reference = {
            "pack": pack_name,
            "pack_sha256": pack_sha256,
            "pack_size": pack_size,
        }
        for name, _, _ in group:
            references[name] = {**reference, "member": name}
    return references


def packed_manifest(manifest: dict, references: dict[str, dict]) -> dict:
    packed = json.loads(json.dumps(manifest))
    packed["format"] = PACKED_FORMAT
    for record in packed["chunks"]:
        if record["source"] == "asset":
            try:
                record.update(references[record["asset"]])
            except KeyError as error:
                raise ValueError("release pack is missing a referenced chunk") from error
    return packed


def referenced_pack_bytes(manifest: dict) -> int:
    packs = {
        record["pack"]: record["pack_size"]
        for record in manifest["chunks"]
        if record["source"] == "asset"
    }
    return sum(packs.values())


def release(target: Path, output: Path, version: str, base: Path | None = None,
            base_version: str | None = None, *, threshold: float = 0.85,
            max_asset_size: int = MAX_ASSET_SIZE,
            pack_target_size: int | None = None) -> dict:
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
    if pack_target_size is None:
        pack_target_size = PACK_TARGET_SIZE
    if type(pack_target_size) is not int or pack_target_size <= 0:
        raise ValueError("invalid pack target size")
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
        delta = None
        delta_manifest = stage / "ota-delta.json"
        if base is not None:
            delta = plan(target, stage / "delta-work", base, max_asset_size=max_asset_size)

        # Gather shared content-addressed chunks before packing so full and
        # delta manifests can refer to the same release asset where possible.
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

        references = pack_assets(
            stage, assets, max_asset_size=max_asset_size,
            pack_target_size=pack_target_size,
        )
        full = packed_manifest(full, references)
        write_json(full_manifest, full)
        if delta is not None:
            delta = packed_manifest(delta, references)
            write_json(delta_manifest, delta)
        for path in assets.values():
            path.unlink()

        full_transfer_bytes = referenced_pack_bytes(full) + full_manifest.stat().st_size
        delta_bytes = None
        if delta is not None:
            delta_bytes = referenced_pack_bytes(delta) + delta_manifest.stat().st_size
        full_estimate = compressed_size(target)
        mode = "delta" if delta_bytes is not None and full_transfer_bytes and delta_bytes < full_transfer_bytes * threshold else "full"

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
            "full_transfer_bytes": full_transfer_bytes,
            "full_compressed_estimate_bytes": full_estimate,
            "verified_variants": verified,
            "files": files,
            "asset_count": len(files) + 1,
        }
        if metadata["asset_count"] > MAX_GITHUB_RELEASE_ASSETS:
            raise ValueError(
                f"release asset count {metadata['asset_count']} exceeds GitHub's "
                f"{MAX_GITHUB_RELEASE_ASSETS}-asset limit"
            )
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
