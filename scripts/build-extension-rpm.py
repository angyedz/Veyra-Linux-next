#!/usr/bin/env python3
"""Build a Veyra extension RPM using the same Fedora package set as the OS."""

from __future__ import annotations

import argparse
import os
import posixpath
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCES = ROOT / "packages" / "extensions"
MKOSI_CONFIG = ROOT / "mkosi.conf"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)


def package_set_from_mkosi() -> list[str]:
    in_content = False
    in_packages = False
    packages: list[str] = []
    for raw_line in MKOSI_CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("[") and line.endswith("]"):
            in_content = line == "[Content]"
            in_packages = False
            continue
        if not in_content or not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("Packages="):
            packages.append(line.split("=", 1)[1].strip())
            in_packages = True
        elif in_packages and line[:1].isspace():
            packages.append(line.strip())
        else:
            in_packages = False
    result = list(dict.fromkeys(package for package in packages if package))
    if not result:
        raise RuntimeError("Could not find the Veyra base package set in mkosi.conf.")
    return result


def installed_packages(root: Path) -> dict[str, str]:
    output = run(
        ["rpm", "--root", str(root), "-qa", "--qf", "%{NAME}\t%{NEVRA}\n"],
        stdout=subprocess.PIPE,
    ).stdout
    packages: dict[str, str] = {}
    for line in output.splitlines():
        name, nevra = line.split("\t", 1)
        packages[name] = nevra
    return packages


def installed_files(root: Path) -> set[str]:
    output = run(
        ["rpm", "--root", str(root), "-qa", "--qf", "[%{FILENAMES}\n]"],
        stdout=subprocess.PIPE,
    ).stdout
    return {line for line in output.splitlines() if line}


def parse_release() -> str:
    section = ""
    for line in MKOSI_CONFIG.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Distribution" and line.startswith("Release="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Could not read Fedora release from mkosi.conf.")


def copy_extension_files(
    root: Path, extension_root: Path, new_packages: list[str], base_files: set[str]
) -> None:
    copied = 0
    for nevra in new_packages:
        listing = run(
            ["rpm", "--root", str(root), "-ql", nevra],
            stdout=subprocess.PIPE,
        ).stdout
        for path in listing.splitlines():
            if not path.startswith(("/usr/", "/opt/")):
                continue
            source = root / path.lstrip("/")
            if not source.exists() and not source.is_symlink():
                continue
            if source.is_dir() and not source.is_symlink():
                continue
            if path in base_files:
                raise RuntimeError(
                    f"Package {nevra} would replace a base-system file: {path}. "
                    "System updates must use the A/B image path."
                )
            target = extension_root / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                link = os.readlink(source)
                normalized_target = (
                    posixpath.normpath(link)
                    if link.startswith("/")
                    else posixpath.normpath(posixpath.join(posixpath.dirname(path), link))
                )
                if not normalized_target.startswith(("/usr/", "/opt/")):
                    raise RuntimeError(f"Extension symlink leaves /usr and /opt: {path} -> {link}")
                target.symlink_to(link)
            elif source.is_file():
                shutil.copy2(source, target, follow_symlinks=False)
            else:
                raise RuntimeError(f"Unsupported extension file type: {path}")
            copied += 1
    if copied == 0:
        raise RuntimeError("The extension package set produced no files under /usr or /opt.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("main", "testing"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    release = parse_release()
    package_names = (PACKAGE_SOURCES / "veyra-shell-tools.packages").read_text(
        encoding="utf-8"
    ).splitlines()
    package_names = [
        line.strip() for line in package_names
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not package_names:
        raise RuntimeError("The shell-tools extension has no Fedora package list.")

    required_commands = ("dnf5", "rpm", "rpmbuild", "mksquashfs")
    missing = [command for command in required_commands if shutil.which(command) is None]
    if missing:
        raise RuntimeError("Install required build tools: " + ", ".join(missing))

    with tempfile.TemporaryDirectory(prefix="veyra-extension-build-") as temporary:
        work = Path(temporary)
        root = work / "root"
        extension_root = work / "extension"
        topdir = work / "rpmbuild"
        for directory in (
            root, extension_root, topdir / "BUILD", topdir / "RPMS",
            topdir / "SOURCES", topdir / "SPECS", topdir / "SRPMS",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        dnf_args = [
            "dnf5",
            "--use-host-config",
            "--installroot=" + str(root),
            "--releasever=" + release,
            "--forcearch=x86_64",
            "--setopt=install_weak_deps=False",
            "--setopt=tsflags=nodocs",
            "--assumeyes",
            "install",
        ]
        run([*dnf_args, *package_set_from_mkosi()])
        base_packages = installed_packages(root)
        base_files = installed_files(root)

        run([*dnf_args, *package_names])
        final_packages = installed_packages(root)
        changed_base = [
            name for name, nevra in base_packages.items()
            if final_packages.get(name) != nevra
        ]
        if changed_base:
            raise RuntimeError(
                "Extension dependencies would change base packages: "
                + ", ".join(changed_base)
                + ". Update the OS image first, then rebuild the extension."
            )
        new_packages = sorted(
            nevra for name, nevra in final_packages.items() if name not in base_packages
        )
        copy_extension_files(root, extension_root, new_packages, base_files)

        release_metadata = extension_root / "usr/lib/extension-release.d/extension-release.veyra-shell-tools"
        release_metadata.parent.mkdir(parents=True, exist_ok=True)
        release_metadata.write_text(f"ID=fedora\nVERSION_ID={release}\n", encoding="utf-8")

        raw_image = topdir / "SOURCES/veyra-shell-tools.raw"
        run(["mksquashfs", str(extension_root), str(raw_image), "-noappend", "-all-root", "-no-progress"])
        spec = PACKAGE_SOURCES / "veyra-shell-tools.spec"
        run([
            "rpmbuild", "-bb", str(spec),
            "--define", f"_topdir {topdir}",
            "--define", f"_sourcedir {topdir / 'SOURCES'}",
            "--define", "_build_name_fmt %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}.rpm",
        ])

        built = list((topdir / "RPMS/x86_64").glob("*.rpm"))
        if len(built) != 1:
            raise RuntimeError(f"Expected one RPM from the shell-tools spec, found {len(built)}.")
        arguments.output.mkdir(parents=True, exist_ok=True)
        for stale in arguments.output.glob("veyra-shell-tools-*.rpm"):
            stale.unlink()
        destination = arguments.output / built[0].name
        shutil.copy2(built[0], destination)
        print(f"Built {destination} for the {arguments.channel} package channel.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"build-extension-rpm: {error}", file=os.sys.stderr)
        raise SystemExit(1)
