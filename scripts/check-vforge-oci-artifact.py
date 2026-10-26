#!/usr/bin/env python3
"""Validate the Vforge Veyra OCI archive and its source configuration."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


EXPECTED_EXTRA_TREES = {
    ("mkosi.extra/usr/bin/vpkg", "/usr/bin/vpkg"),
    ("mkosi.extra/usr/lib/veyra/vpkg_libdnf.py", "/usr/lib/veyra/vpkg_libdnf.py"),
    ("mkosi.extra/usr/lib/veyra/veyra_repo.py", "/usr/lib/veyra/veyra_repo.py"),
    ("mkosi.extra/usr/share/veyra/vpkg/protected-core.json", "/usr/share/veyra/vpkg/protected-core.json"),
    ("mkosi.extra/etc/yum.repos.d/rpmfusion.repo", "/etc/yum.repos.d/rpmfusion.repo"),
    ("mkosi.extra/usr/share/pki/rpm-gpg", "/usr/share/pki/rpm-gpg"),
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _config_values(path: Path) -> dict[str, list[str]]:
    section = ""
    result: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, separator, value = line.partition("=")
        if separator:
            result.setdefault(f"{section}.{key.strip()}", []).append(value.strip())
    return result


def validate_source_config(root: Path, *, require_sources: bool = False) -> list[str]:
    root = root.resolve()
    main_path = root / "mkosi.conf"
    base_path = root / "mkosi.veyra-base.conf"
    image_path = root / "mkosi.images/veyra-oci-rootfs/mkosi.conf"
    main = _config_values(main_path)
    image = _config_values(image_path)
    base = _config_values(base_path)

    if "mkosi.veyra-base.conf" not in main.get("Include.Include", []):
        raise ValueError("The installed Veyra image does not include the shared package definition.")
    if "%D/mkosi.veyra-base.conf" not in image.get("Include.Include", []):
        raise ValueError("The OCI rootfs does not include the shared package definition.")
    if "veyra-oci-rootfs" not in main.get("Config.Dependencies", []):
        raise ValueError("The installed Veyra image does not build the OCI rootfs subimage.")
    if image.get("Output.Format") != ["oci-archive"] or image.get("Content.Bootable") != ["no"]:
        raise ValueError("The Vforge rootfs must be a non-bootable OCI archive image.")
    if image.get("Output.ImageId") or image.get("Output.ImageVersion"):
        raise ValueError("The OCI rootfs must inherit ImageId and ImageVersion from the installed Veyra image.")
    packages = base.get("Content.Packages", [])
    if not packages or len(packages) != len(set(packages)):
        raise ValueError("The shared Veyra package definition is empty or contains duplicate packages.")
    if any(main.get(f"Content.{key}") or image.get(f"Content.{key}") for key in ("Packages",)):
        raise ValueError("Main and OCI images must take their package inventory from the shared include.")

    trees: set[tuple[str, str]] = set()
    for assignment in image.get("Content.ExtraTrees", []):
        source, separator, target = assignment.partition(":")
        if not separator:
            raise ValueError(f"OCI ExtraTrees entry must specify an image target: {assignment}")
        if not source.startswith("%D/"):
            raise ValueError(f"OCI ExtraTrees source must be repository-relative: {source}")
        resolved_source = (root / source.removeprefix("%D/")).resolve(strict=False)
        try:
            relative_source = resolved_source.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError(f"OCI image references a host-state source outside the checkout: {source}") from error
        if target in {"/", "/etc", "/var", "/home"}:
            raise ValueError(f"OCI ExtraTrees target is too broad: {target}")
        trees.add((relative_source, target))
        if require_sources and not resolved_source.exists():
            raise ValueError(f"Required OCI runtime source is missing: {relative_source}")
    if trees != EXPECTED_EXTRA_TREES:
        raise ValueError("OCI rootfs ExtraTrees do not match the reviewed Vpkg/runtime allowlist.")
    if image.get("Content.BaseTrees") or image.get("Content.SkeletonTrees"):
        raise ValueError("OCI rootfs must not copy host or installed-image state through BaseTrees/SkeletonTrees.")
    if require_sources:
        vpkg = (root / "mkosi.extra/usr/bin/vpkg").read_text(encoding="utf-8")
        if "container" not in vpkg:
            raise ValueError("Vpkg source does not provide the container-mode CLI.")
        policy_path = root / "mkosi.extra/usr/share/veyra/vpkg/protected-core.json"
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Vpkg protected-core policy is missing or invalid.") from error
        if not isinstance(policy, dict):
            raise ValueError("Vpkg protected-core policy must be a JSON object.")
        repo = (root / "mkosi.extra/etc/yum.repos.d/rpmfusion.repo").read_text(encoding="utf-8")
        if not all(name in repo for name in ("rpmfusion-free", "rpmfusion-nonfree")):
            raise ValueError("The OCI rootfs RPMFusion repository configuration is incomplete.")
        key_dir = root / "mkosi.extra/usr/share/pki/rpm-gpg"
        if not all((key_dir / name).is_file() for name in (
            "RPM-GPG-KEY-rpmfusion-free-fedora-2020",
            "RPM-GPG-KEY-rpmfusion-nonfree-fedora-2020",
        )):
            raise ValueError("The OCI rootfs RPMFusion signing keys are incomplete.")
    return packages


def _safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe path in OCI archive: {name}")
    return str(path).removeprefix("./")


def _read_tar_member(archive: tarfile.TarFile, names: dict[str, tarfile.TarInfo], name: str) -> bytes:
    info = names.get(name)
    if info is None or not info.isfile():
        raise ValueError(f"OCI artifact is missing required file: {name}")
    stream = archive.extractfile(info)
    if stream is None:
        raise ValueError(f"OCI artifact file cannot be read: {name}")
    return stream.read()


def _read_layer(outer: tarfile.TarFile, names: dict[str, tarfile.TarInfo], layer: dict[str, object]) -> bytes:
    digest = layer.get("digest")
    media_type = layer.get("mediaType")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or not isinstance(media_type, str):
        raise ValueError("OCI manifest layer descriptor is malformed.")
    digest_hex = digest.removeprefix("sha256:")
    if not HEX64.fullmatch(digest_hex):
        raise ValueError("OCI manifest layer digest is malformed.")
    compressed = _read_tar_member(outer, names, f"blobs/sha256/{digest_hex}")
    if hashlib.sha256(compressed).hexdigest() != digest_hex:
        raise ValueError("OCI rootfs layer digest does not match its manifest.")
    if media_type.endswith("+gzip"):
        return gzip.decompress(compressed)
    if media_type.endswith("+tar"):
        return compressed
    raise ValueError(f"Unsupported OCI rootfs layer media type: {media_type}")


def _identity_fields(content: bytes) -> dict[str, str]:
    fields = {}
    for line in content.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key] = value.strip().strip('"')
    return fields


def validate_oci_archive(path: Path) -> tuple[list[str], str]:
    with tarfile.open(path, mode="r:*") as outer:
        outer_names = {_safe_name(item.name): item for item in outer.getmembers()}
        layout = json.loads(_read_tar_member(outer, outer_names, "oci-layout"))
        if layout.get("imageLayoutVersion") != "1.0.0":
            raise ValueError("OCI archive has an unsupported image layout version.")
        index = json.loads(_read_tar_member(outer, outer_names, "index.json"))
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1 or not isinstance(manifests[0], dict):
            raise ValueError("OCI index must describe exactly one Veyra image manifest.")
        descriptor = manifests[0]
        index_annotations = descriptor.get("annotations", {})
        if index_annotations.get("org.opencontainers.image.ref.name") != "veyra-next":
            raise ValueError("OCI image reference name is not inherited from Veyra ImageId.")
        manifest_digest = descriptor.get("digest")
        if not isinstance(manifest_digest, str) or not manifest_digest.startswith("sha256:"):
            raise ValueError("OCI index manifest digest is malformed.")
        manifest_hex = manifest_digest.removeprefix("sha256:")
        if not HEX64.fullmatch(manifest_hex):
            raise ValueError("OCI index manifest digest is malformed.")
        manifest_bytes = _read_tar_member(outer, outer_names, f"blobs/sha256/{manifest_hex}")
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_hex:
            raise ValueError("OCI manifest digest does not match its descriptor.")
        manifest = json.loads(manifest_bytes)
        annotations = manifest.get("annotations", {})
        version = annotations.get("org.opencontainers.image.version")
        if not isinstance(version, str) or not version:
            raise ValueError("OCI manifest does not identify its inherited ImageVersion.")
        if annotations.get("org.veyra.base.id") != "veyra-next" or annotations.get("org.veyra.base.version") != version:
            raise ValueError("OCI manifest Veyra base identity is missing or inconsistent.")
        config_descriptor = manifest.get("config", {})
        config_digest = config_descriptor.get("digest")
        if not isinstance(config_digest, str) or not config_digest.startswith("sha256:"):
            raise ValueError("OCI image configuration descriptor is malformed.")
        config_hex = config_digest.removeprefix("sha256:")
        config_bytes = _read_tar_member(outer, outer_names, f"blobs/sha256/{config_hex}")
        if hashlib.sha256(config_bytes).hexdigest() != config_hex:
            raise ValueError("OCI image configuration digest does not match its descriptor.")
        config = json.loads(config_bytes)
        labels = config.get("config", {}).get("Labels", {})
        if (
            labels.get("org.veyra.base.id") != "veyra-next"
            or labels.get("org.veyra.base.version") != version
            or labels.get("org.veyra.base.schema") != "1"
        ):
            raise ValueError("OCI config labels do not carry the Veyra base identity.")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or len(layers) != 1 or not isinstance(layers[0], dict):
            raise ValueError("Veyra OCI rootfs must contain exactly one rootfs layer.")
        rootfs_layer = _read_layer(outer, outer_names, layers[0])
        diff_ids = config.get("rootfs", {}).get("diff_ids", [])
        if diff_ids != [f"sha256:{hashlib.sha256(rootfs_layer).hexdigest()}"]:
            raise ValueError("OCI rootfs diff ID does not match the uncompressed layer.")

    rpm_names: set[str] = set()
    files: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(rootfs_layer), mode="r:") as layer_archive:
        for item in layer_archive.getmembers():
            name = _safe_name(item.name)
            files.add(name)
            if name.startswith("usr/lib/sysimage/rpm/") and item.isfile():
                rpm_names.add(name)
        if not rpm_names:
            raise ValueError("OCI rootfs has no private RPM database under /usr/lib/sysimage/rpm.")
        layer_names = {_safe_name(item.name): item for item in layer_archive.getmembers()}
        os_release = _read_tar_member(layer_archive, layer_names, "usr/lib/os-release")
    identity = _identity_fields(os_release)
    if identity.get("ID") != "fedora" or identity.get("VERSION_ID") != "44":
        raise ValueError("OCI rootfs is not based on Fedora 44.")
    if identity.get("IMAGE_ID") != "veyra-next" or identity.get("IMAGE_VERSION") != version:
        raise ValueError("OCI rootfs os-release identity does not match its OCI metadata.")
    required_files = {
        "usr/bin/vpkg",
        "usr/lib/veyra/vpkg_libdnf.py",
        "usr/lib/veyra/veyra_repo.py",
        "usr/share/veyra/vpkg/protected-core.json",
        "etc/yum.repos.d/rpmfusion.repo",
        "usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-free-fedora-2020",
        "usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-nonfree-fedora-2020",
    }
    if missing := required_files - files:
        raise ValueError("OCI rootfs is missing Vpkg/runtime files: " + ", ".join(sorted(missing)))
    vpkg_runtime = PurePosixPath("usr/lib/veyra")
    unexpected_bytecode = sorted(
        name for name in files
        if PurePosixPath(name).is_relative_to(vpkg_runtime)
        and (PurePosixPath(name).suffix in {".pyc", ".pyo"} or "__pycache__" in PurePosixPath(name).parts)
    )
    if unexpected_bytecode:
        raise ValueError("OCI Vpkg runtime must not contain Python bytecode: " + ", ".join(unexpected_bytecode))
    return sorted(rpm_names), manifest_digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--config-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-sources", action="store_true")
    args = parser.parse_args()
    packages = validate_source_config(args.config_root, require_sources=args.require_sources)
    rpm_files, digest = validate_oci_archive(args.archive)
    print(
        f"Validated Veyra Fedora 44 OCI rootfs: versioned image identity, "
        f"{len(packages)} shared package inputs, {len(rpm_files)} private RPM DB files, "
        f"manifest {digest}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
