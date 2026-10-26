#!/usr/bin/env python3
"""Require an isolated Fedora container or the guarded Ubuntu runner with pinned Fedora tools."""

from __future__ import annotations

import configparser
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BuildEnvironmentError(RuntimeError):
    pass


def key_values(path: pathlib.Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key] = value.strip().strip('"')
    return fields


def target_release(config: pathlib.Path) -> str:
    section = ""
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Distribution" and line.startswith("Release="):
            release = line.partition("=")[2]
            if not release.isdigit():
                break
            return release
    raise BuildEnvironmentError("mkosi.conf must declare a numeric Fedora target Release")


def target_distribution(config: pathlib.Path) -> str:
    section = ""
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Distribution" and line.startswith("Distribution="):
            return line.partition("=")[2].strip()
    raise BuildEnvironmentError("mkosi.conf must declare a target Distribution")


def tools_tree_target(config: pathlib.Path) -> tuple[str, str, bool]:
    section = ""
    values: dict[str, str] = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Build" and "=" in line:
            key, _, value = line.partition("=")
            if key in {"ToolsTree", "ToolsTreeDistribution", "ToolsTreeRelease"}:
                values[key] = value.strip()
    return (
        values.get("ToolsTreeDistribution", ""),
        values.get("ToolsTreeRelease", ""),
        values.get("ToolsTree", "").lower() in {"1", "yes", "true"},
    )


def enabled_repo_ids(repo_directory: pathlib.Path) -> dict[str, tuple[str, dict[str, str]]]:
    repos: dict[str, tuple[str, dict[str, str]]] = {}
    for path in sorted(repo_directory.glob("*.repo")):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error as error:
            raise BuildEnvironmentError(f"invalid DNF repository file {path}: {error}") from error
        for name in parser.sections():
            values = dict(parser.items(name))
            if values.get("enabled", "1").strip() == "1":
                if name in repos:
                    raise BuildEnvironmentError(f"duplicate enabled repository ID: {name}")
                repos[name] = (str(path), values)
    return repos


def validate_environment(
    *,
    osrelease_path: pathlib.Path,
    repo_directory: pathlib.Path,
    mkosi_config: pathlib.Path,
    enabled_repo_listing: str,
    mountinfo: str,
    in_container: bool,
    host_runner: bool = False,
    host_dnf_available: bool = False,
    host_dnf_ubuntu_owned: bool = False,
) -> None:
    if not in_container and not host_runner:
        raise BuildEnvironmentError("image composition must run inside an isolated Fedora container")

    release = target_release(mkosi_config)
    distribution = target_distribution(mkosi_config)
    if distribution != "fedora":
        raise BuildEnvironmentError(f"target Distribution must be fedora; found {distribution!r}")
    tools_distribution, tools_release, tools_enabled = tools_tree_target(mkosi_config)
    if not tools_enabled or tools_distribution != "fedora" or tools_release != release:
        raise BuildEnvironmentError(
            "mkosi tools tree must be enabled and pinned to the target Fedora release; "
            f"target={release!r}, tools_distribution={tools_distribution!r}, "
            f"tools_release={tools_release!r}, enabled={tools_enabled}"
        )
    osrelease = key_values(osrelease_path)
    if host_runner:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
            raise BuildEnvironmentError("Ubuntu host builds are restricted to GitHub Actions Linux runners")
        if osrelease.get("ID") != "ubuntu" or osrelease.get("VERSION_ID") != "24.04":
            raise BuildEnvironmentError(
                f"build runner must be Ubuntu 24.04; found "
                f"ID={osrelease.get('ID')!r}, VERSION_ID={osrelease.get('VERSION_ID')!r}"
            )
        configured = enabled_repo_ids(repo_directory)
        listed = {
            line.split()[0]
            for line in enabled_repo_listing.splitlines()[1:]
            if line.split()
        }
        repo_files = list(repo_directory.glob("*.repo")) + list(pathlib.Path("/etc/dnf/repos.d").glob("*.repo"))
        if not host_dnf_available or not host_dnf_ubuntu_owned:
            raise BuildEnvironmentError("Ubuntu host builds require Ubuntu-packaged /usr/bin/dnf")
        if configured or listed or repo_files:
            raise BuildEnvironmentError("Ubuntu host DNF must not have host repositories or repo files")
    elif osrelease.get("ID") != "fedora" or osrelease.get("VERSION_ID") != release:
        raise BuildEnvironmentError(
            f"build container must be Fedora {release}; found "
            f"ID={osrelease.get('ID')!r}, VERSION_ID={osrelease.get('VERSION_ID')!r}"
        )

    if not host_runner:
        expected = {
            "fedora": f"https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch",
            "updates": "https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever&arch=$basearch",
            "fedora-cisco-openh264": f"https://mirrors.fedoraproject.org/metalink?repo=fedora-cisco-openh264-$releasever&arch=$basearch",
        }
        configured = enabled_repo_ids(repo_directory)
        listed = {
            line.split()[0]
            for line in enabled_repo_listing.splitlines()[1:]
            if line.split()
        }
        if set(configured) != set(expected) or listed != set(expected):
            raise BuildEnvironmentError(
                "enabled repos must be only official Fedora repos for the current target "
                f"(fedora, updates); config={sorted(configured)!r}, dnf={sorted(listed)!r}"
            )
        for repo, expected_url in expected.items():
            values = configured[repo][1]
            urls = [values.get(key, "").strip() for key in ("metalink", "mirrorlist", "baseurl")]
            if urls.count(expected_url) != 1 or sum(bool(url) for url in urls) != 1:
                raise BuildEnvironmentError(
                    f"Fedora repository URL for {repo} must be exactly {expected_url!r}; found {urls!r}"
                )

    home = pathlib.Path(os.environ.get("HOME", "/root")).resolve()
    protected = {
        pathlib.Path("/etc/dnf"),
        pathlib.Path("/etc/yum.repos.d"),
        pathlib.Path("/var/cache/dnf"),
        pathlib.Path("/var/cache/libdnf5"),
        pathlib.Path("/var/lib/dnf"),
        pathlib.Path("/var/lib/libdnf5"),
        pathlib.Path("/root/.cache"),
        home / ".cache",
    }
    for line in mountinfo.splitlines():
        fields = line.partition(" - ")[0].split()
        if len(fields) < 5:
            continue
        mountpoint = pathlib.Path(fields[4].replace("\\040", " ")).resolve()
        if os.environ.get("GITHUB_ACTIONS") == "true" and mountpoint == pathlib.Path("/github/home"):
            # GitHub Actions bind-mounts this directory as the job container's HOME.
            # The actual cache directory (/github/home/.cache) remains blocked below.
            continue
        if mountpoint != pathlib.Path("/") and (
            any(part in {"cache", ".cache", "dnf", "libdnf5", "mkosi"} for part in mountpoint.parts)
            or any(
            mountpoint == path or mountpoint in path.parents or path in mountpoint.parents
            for path in protected
            )
        ):
            raise BuildEnvironmentError(f"host DNF/cache mount detected at {mountpoint}")


def main() -> int:
    in_container = pathlib.Path("/.dockerenv").exists() or pathlib.Path("/run/.containerenv").exists()
    host_runner = os.environ.get("VEYRA_MKOSI_RUNNER") == "github-ubuntu-24.04"
    try:
        if host_runner:
            listing = subprocess.check_output(
                ["dnf", "repolist", "--enabled", "--quiet", "--cacheonly", "--releasever=44"],
                text=True,
            )
            dnf_path = shutil.which("dnf") or ""
            owner = subprocess.check_output(["dpkg-query", "-S", "/usr/bin/dnf"], text=True)
            dnf_ubuntu_owned = dnf_path == "/usr/bin/dnf" and any(
                line.startswith("dnf: /usr/bin/dnf") for line in owner.splitlines()
            )
        else:
            listing = subprocess.check_output(
                ["dnf5", "repo", "list", "--enabled", "--quiet"], text=True
            )
            dnf_ubuntu_owned = False
        validate_environment(
            osrelease_path=pathlib.Path("/etc/os-release"),
            repo_directory=pathlib.Path("/etc/yum.repos.d"),
            mkosi_config=ROOT / "mkosi.conf",
            enabled_repo_listing=listing,
            mountinfo=pathlib.Path("/proc/self/mountinfo").read_text(encoding="utf-8"),
            in_container=in_container,
            host_runner=host_runner,
            host_dnf_available=host_runner and shutil.which("dnf") is not None,
            host_dnf_ubuntu_owned=host_runner and dnf_ubuntu_owned,
        )
    except (OSError, subprocess.CalledProcessError, BuildEnvironmentError) as error:
        print(f"mkosi build environment gate failed: {error}", file=sys.stderr)
        return 1
    if host_runner:
        print(f"Ubuntu runner and Fedora {target_release(ROOT / 'mkosi.conf')} mkosi tools tree are valid")
    else:
        print(f"Isolated Fedora {target_release(ROOT / 'mkosi.conf')} build environment is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
