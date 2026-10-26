#!/usr/bin/env python3
"""Inject a signed vpkg fixture and persistence markers into an image copy."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME_PARTITION_TYPE = "933ac7e1-2eb4-4f13-b844-0e14e2aef915"
UPSTREAM_REPOSITORIES = (
    "fedora",
    "updates",
    "rpmfusion-free",
    "rpmfusion-free-updates",
    "rpmfusion-nonfree",
    "rpmfusion-nonfree-updates",
)


def run(command: list[str], *, privileged: bool = False) -> subprocess.CompletedProcess[str]:
    if privileged and os.geteuid() != 0:
        command = ["sudo", *command]
    return subprocess.run(command, check=True, text=True, capture_output=True)


def partition_layout(image: pathlib.Path) -> dict[str, tuple[int, int]]:
    result = run(["sfdisk", "--json", str(image)])
    table = json.loads(result.stdout)["partitiontable"]
    sector_size = int(table.get("sectorsize", 512))
    partitions: dict[str, tuple[int, int]] = {}
    for partition in table["partitions"]:
        name = partition.get("name")
        partition_name = str(name) if name in {"VEYRA_ETC", "VEYRA_VAR"} else None
        if str(partition.get("type", "")).lower() == HOME_PARTITION_TYPE:
            partition_name = "VEYRA_HOME"
        if partition_name:
            partitions[partition_name] = (
                int(partition["start"]) * sector_size,
                int(partition["size"]) * sector_size,
            )
    if set(partitions) != {"VEYRA_ETC", "VEYRA_VAR", "VEYRA_HOME"}:
        raise RuntimeError("The Veyra image must contain persistent /etc, /var and /home partitions.")
    return partitions


def install_file(source: pathlib.Path, destination: pathlib.Path, mode: str = "0644") -> None:
    run(["install", "-D", "-m", mode, str(source), str(destination)], privileged=True)


def unit_test_sources() -> list[pathlib.Path]:
    """Return the source files required by the guest tests and their imports."""
    return [
        pathlib.Path("mkosi.conf"),
        pathlib.Path("scripts/build-extension-rpm.py"),
        pathlib.Path("scripts/check-build-environment.py"),
        pathlib.Path("scripts/check-image.py"),
        pathlib.Path("scripts/mirror-package-site.py"),
        pathlib.Path("scripts/prepare-vpkg-image-test.py"),
        pathlib.Path("scripts/test-ab-boot.py"),
        pathlib.Path("scripts/test-sysupdate-image.py"),
        pathlib.Path("mkosi.extra/usr/bin/vpkg"),
        pathlib.Path("mkosi.extra/usr/lib/veyra/veyra_repo.py"),
        pathlib.Path("mkosi.extra/usr/lib/veyra/vpkg_libdnf.py"),
        pathlib.Path("mkosi.extra/usr/lib/sysupdate.d/10-root-verity.transfer"),
        pathlib.Path("mkosi.extra/usr/lib/sysupdate.d/20-root.transfer"),
        pathlib.Path("mkosi.extra/usr/lib/sysupdate.d/99-uki.transfer"),
    ]


def inject(image: pathlib.Path, repository: pathlib.Path) -> None:
    if not image.is_file():
        raise RuntimeError(f"Missing Veyra disk image: {image}")
    if not (repository / "RPM-GPG-KEY-VEYRA").is_file():
        raise RuntimeError(f"The signed vpkg fixture is missing its public key: {repository}")
    for repo in ("testing-initial", "testing"):
        if not (repository / f"packages/{repo}/x86_64/repodata/repomd.xml.asc").is_file():
            raise RuntimeError(f"The vpkg fixture must contain signed {repo} metadata.")
    upstream = repository / "local-upstream/x86_64"
    if not (upstream / "repodata/repomd.xml").is_file() or not list(upstream.glob("*.rpm")):
        raise RuntimeError("The vpkg fixture must contain an offline Fedora RPM repository.")

    partitions = partition_layout(image)
    with tempfile.TemporaryDirectory(prefix="veyra-vpkg-image-setup-") as temporary:
        work = pathlib.Path(temporary)
        mounts = {name: work / name.lower() for name in partitions}
        for mountpoint in mounts.values():
            mountpoint.mkdir()

        mounted: list[pathlib.Path] = []
        try:
            for name, mountpoint in mounts.items():
                offset, size = partitions[name]
                run([
                    "mount", "-t", "ext4", "-o",
                    f"loop,rw,nodev,nosuid,offset={offset},sizelimit={size}",
                    str(image), str(mountpoint),
                ], privileged=True)
                mounted.append(mountpoint)

            etc = mounts["VEYRA_ETC"]
            var = mounts["VEYRA_VAR"]
            repo_target = var / "lib/veyra/vpkg-ci-repo"
            run(["install", "-d", "-m", "0755", str(repo_target)], privileged=True)
            run(["cp", "-a", str(repository.resolve()) + "/.", str(repo_target)], privileged=True)

            unit_test_root = var / "lib/veyra/vpkg-ci-tests/repo"
            run(["install", "-d", "-m", "0755", str(unit_test_root)], privileged=True)
            run(["cp", "-a", str(PROJECT_ROOT / "tests"), str(unit_test_root)], privileged=True)
            test_sources = unit_test_sources()
            vforge_test = PROJECT_ROOT / "tests/test_vforge.py"
            vforge_script = pathlib.Path("mkosi.extra/usr/bin/vforge")
            if vforge_test.is_file():
                if not (PROJECT_ROOT / vforge_script).is_file():
                    raise RuntimeError(
                        "Vforge QEMU unit tests require mkosi.extra/usr/bin/vforge."
                    )
                test_sources.append(vforge_script)
            for relative in test_sources:
                destination = unit_test_root / relative
                run([
                    "install", "-D", "-m", "0644",
                    str(PROJECT_ROOT / relative), str(destination),
                ], privileged=True)

            main_repo = work / "veyra-main.repo"
            main_repo.write_text(
                "[veyra-main]\n"
                "name=Veyra main disposable VM test\n"
                "baseurl=file:///var/lib/veyra/vpkg-ci-repo/packages/main/$basearch/\n"
                "enabled=0\n"
                "gpgcheck=1\n"
                "repo_gpgcheck=1\n"
                "gpgkey=file:///usr/share/veyra/RPM-GPG-KEY-VEYRA\n"
                "metadata_expire=0\n",
                encoding="utf-8",
            )
            install_file(main_repo, etc / "yum.repos.d/veyra-main.repo")
            test_repo = work / "veyra-testing.repo"
            test_repo.write_text(
                "[veyra-testing]\n"
                "name=Veyra testing disposable VM test\n"
                "baseurl=file:///var/lib/veyra/vpkg-ci-repo/packages/testing-initial/$basearch/\n"
                "enabled=0\n"
                "gpgcheck=1\n"
                "repo_gpgcheck=1\n"
                "gpgkey=file:///usr/share/veyra/RPM-GPG-KEY-VEYRA\n"
                "metadata_expire=0\n",
                encoding="utf-8",
            )
            install_file(test_repo, etc / "yum.repos.d/veyra-testing.repo")

            for repo_file in (etc / "yum.repos.d").glob("*.repo"):
                if repo_file.name.startswith("veyra-"):
                    continue
                content = repo_file.read_text(encoding="utf-8", errors="replace")
                if any(f"[{repository_id}]" in content for repository_id in UPSTREAM_REPOSITORIES):
                    repo_file.unlink()
            upstream_repo = work / "veyra-vpkg-ci-upstream.repo"
            baseurl = "file:///var/lib/veyra/vpkg-ci-repo/local-upstream/x86_64/"
            sections = []
            for repository_id in UPSTREAM_REPOSITORIES:
                sections.append(
                    f"[{repository_id}]\n"
                    f"name=Veyra offline vpkg fixture ({repository_id})\n"
                    f"baseurl={baseurl}\n"
                    "enabled=1\n"
                    "gpgcheck=1\n"
                    "repo_gpgcheck=0\n"
                    "metadata_expire=0\n"
                )
            upstream_repo.write_text("\n".join(sections), encoding="utf-8")
            install_file(upstream_repo, etc / "yum.repos.d/veyra-vpkg-ci-upstream.repo")

            dnf_config = work / "dnf.conf"
            dnf_config.write_text(
                "[main]\n"
                "assumeyes=True\n"
                "install_weak_deps=False\n"
                "reposdir=/etc/yum.repos.d\n",
                encoding="utf-8",
            )
            install_file(dnf_config, etc / "dnf/dnf.conf")
            channel = work / "channel"
            channel.write_text("testing\n", encoding="ascii")
            install_file(channel, etc / "veyra/vpkg/channel")

            marker = work / "persistent-state-marker"
            marker.write_text("persistent state created before the A/B update\n", encoding="ascii")
            install_file(marker, etc / "veyra/vpkg-ci-persisted")
            install_file(marker, var / "lib/veyra/vpkg-ci-persisted")
            install_file(marker, mounts["VEYRA_HOME"] / ".veyra-vpkg-ci-persisted")

        finally:
            for mountpoint in reversed(mounted):
                run(["umount", str(mountpoint)], privileged=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True, type=pathlib.Path)
    args = parser.parse_args()
    inject(args.image.resolve(), args.repository.resolve())
    print("Injected the disposable signed vpkg fixture into the image's persistent partitions.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        if detail:
            print(detail, file=sys.stderr)
        print(f"prepare-vpkg-image-test: {error}", file=sys.stderr)
        raise SystemExit(1)
