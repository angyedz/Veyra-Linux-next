from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-vforge-oci-artifact.py"
SPEC = importlib.util.spec_from_file_location("check_vforge_oci_artifact", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _blob(archive: tarfile.TarFile, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    info = tarfile.TarInfo(f"blobs/sha256/{digest}")
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))
    return digest


def _make_oci_archive(path: Path) -> None:
    files = {
        "usr/lib/os-release": b'ID=fedora\nVERSION_ID="44"\nIMAGE_ID=veyra-next\nIMAGE_VERSION=0.1.0\n',
        "usr/lib/sysimage/rpm/Packages": b"private-rpmdb",
        "usr/bin/vpkg": b"vpkg",
        "usr/lib/veyra/vpkg_libdnf.py": b"adapter",
        "usr/lib/veyra/veyra_repo.py": b"repo",
        "usr/share/veyra/vpkg/protected-core.json": b"{}",
        "etc/yum.repos.d/rpmfusion.repo": b"repo",
        "usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-free-fedora-2020": b"key",
        "usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-nonfree-fedora-2020": b"key",
    }
    uncompressed = _tar_bytes(files)
    compressed = gzip.compress(uncompressed)
    config = json.dumps({
        "rootfs": {"type": "layers", "diff_ids": [f"sha256:{hashlib.sha256(uncompressed).hexdigest()}"]},
        "config": {"Labels": {
            "org.veyra.base.id": "veyra-next",
            "org.veyra.base.version": "0.1.0",
            "org.veyra.base.schema": "1",
        }},
    }).encode()
    config_digest = hashlib.sha256(config).hexdigest()
    manifest = json.dumps({
        "schemaVersion": 2,
        "config": {"digest": f"sha256:{config_digest}"},
        "layers": [{
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": f"sha256:{hashlib.sha256(compressed).hexdigest()}",
        }],
        "annotations": {
            "org.opencontainers.image.version": "0.1.0",
            "org.veyra.base.id": "veyra-next",
            "org.veyra.base.version": "0.1.0",
        },
    }).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    index = json.dumps({"schemaVersion": 2, "manifests": [{
        "digest": f"sha256:{manifest_digest}",
        "annotations": {"org.opencontainers.image.ref.name": "veyra-next"},
    }]}).encode()
    with tarfile.open(path, mode="w") as archive:
        for name, content in (("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'), ("index.json", index)):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        _blob(archive, manifest)
        _blob(archive, config)
        _blob(archive, compressed)


class VforgeOciArtifactTests(unittest.TestCase):
    def test_source_configuration_shares_base_packages_and_uses_narrow_extra_trees(self) -> None:
        packages = checker.validate_source_config(ROOT)
        self.assertIn("libdnf5", packages)
        self.assertIn("python3-libdnf5", packages)

    def test_oci_archive_checks_fedora_identity_private_rpmdb_and_vpkg_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "rootfs.tar"
            _make_oci_archive(archive)
            rpmdb_files, _ = checker.validate_oci_archive(archive)
        self.assertEqual(rpmdb_files, ["usr/lib/sysimage/rpm/Packages"])


if __name__ == "__main__":
    unittest.main()
