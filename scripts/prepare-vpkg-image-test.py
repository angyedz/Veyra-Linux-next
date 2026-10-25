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


HOME_PARTITION_TYPE = "933ac7e1-2eb4-4f13-b844-0e14e2aef915"


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


def inject(image: pathlib.Path, repository: pathlib.Path) -> None:
    if not image.is_file():
        raise RuntimeError(f"Missing Veyra disk image: {image}")
    if not (repository / "RPM-GPG-KEY-VEYRA").is_file():
        raise RuntimeError(f"The signed vpkg fixture is missing its public key: {repository}")
    for repo in ("testing-initial", "testing"):
        if not (repository / f"packages/{repo}/x86_64/repodata/repomd.xml.asc").is_file():
            raise RuntimeError(f"The vpkg fixture must contain signed {repo} metadata.")

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

            main_repo = work / "veyra-main.repo"
            main_repo.write_text(
                "[veyra-main]\n"
                "name=Veyra main disposable VM test\n"
                "baseurl=file:///var/lib/veyra/vpkg-ci-repo/packages/main/$basearch/\n"
                "enabled=0\n"
                "gpgcheck=1\n"
                "repo_gpgcheck=1\n"
                "gpgkey=file:///var/lib/veyra/vpkg-ci-repo/RPM-GPG-KEY-VEYRA\n"
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
                "gpgkey=file:///var/lib/veyra/vpkg-ci-repo/RPM-GPG-KEY-VEYRA\n"
                "metadata_expire=0\n",
                encoding="utf-8",
            )
            install_file(test_repo, etc / "yum.repos.d/veyra-testing.repo")

            dnf_config = work / "dnf.conf"
            dnf_config.write_text("[main]\nassumeyes=True\ninstall_weak_deps=False\n", encoding="utf-8")
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
