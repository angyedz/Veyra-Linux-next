#!/usr/bin/env python3
"""Check the source contract for the Veyra Next Fedora image."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def section_values(path: pathlib.Path, section: str) -> list[str]:
    current = ""
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"\[([^]]+)\]", line)
        if match:
            current = match.group(1)
        elif current == section:
            values.append(line)
    return values


def fail(message: str) -> int:
    print(f"Veyra Next smoke check failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    config = ROOT / "mkosi.conf"
    output = section_values(config, "Output")
    content = section_values(config, "Content")
    distribution = section_values(config, "Distribution")
    build = section_values(config, "Build")

    for expected in (
        "Distribution=fedora",
        "Release=44",
        "Architecture=x86-64",
        "RepositoryKeyFetch=yes",
    ):
        if expected not in distribution:
            return fail(f"{config.name} must contain {expected!r} in [Distribution]")
    for expected in ("Format=disk", "Output=veyra-next", "OutputDirectory=mkosi.output"):
        if expected not in output:
            return fail(f"{config.name} must contain {expected!r} in [Output]")
    for expected in ("Bootable=yes", "Bootloader=systemd-boot", "UnifiedKernelImages=unsigned"):
        if expected not in content:
            return fail(f"{config.name} must contain {expected!r} in [Content]")
    if "ToolsTree=yes" not in build:
        return fail(f"{config.name} must build through mkosi's isolated tools tree")

    packages = {line.removeprefix("Packages=") for line in content if line.startswith("Packages=")}
    required_packages = {
        "kernel",
        "NetworkManager",
        "firewalld",
        "flatpak",
        "initial-setup",
        "initial-setup-gui-wayland-plasma",
        "plasma-login-manager",
        "plasma-desktop",
        "plasma-workspace",
        "plasma-workspace-wayland",
        "plasma-discover",
        "plasma-discover-flatpak",
        "xdg-desktop-portal-kde",
        "pipewire",
        "mesa-vulkan-drivers",
        "git",
        "gcc",
        "clang",
        "cmake",
        "python3",
        "nodejs",
    }
    missing = sorted(required_packages - packages)
    if missing:
        return fail("missing image packages: " + ", ".join(missing))

    preset = ROOT / "mkosi.extra/usr/lib/systemd/system-preset/99-veyra.preset"
    preset_content = preset.read_text(encoding="utf-8")
    for service in (
        "enable NetworkManager.service",
        "enable firewalld.service",
        "enable initial-setup.service",
        "enable plasmalogin.service",
    ):
        if service not in preset_content:
            return fail(f"system preset must include {service!r}")

    default_target = ROOT / "mkosi.extra/etc/systemd/system/default.target"
    if not default_target.is_symlink() or default_target.readlink() != pathlib.Path(
        "/usr/lib/systemd/system/graphical.target"
    ):
        return fail("default.target must point to graphical.target")

    postinstall = ROOT / "mkosi.postinst.chroot"
    postinstall_content = postinstall.read_text(encoding="utf-8")
    if "flatpak remote-add --system --if-not-exists flathub" not in postinstall_content:
        return fail("post-install script must configure Flathub for all users")
    remote_definition = ROOT / "mkosi.extra/usr/share/veyra/flathub.flatpakrepo"
    if not remote_definition.is_file():
        return fail("the offline Flathub remote definition must be included in the image")
    remote_content = remote_definition.read_text(encoding="utf-8")
    for expected in ("[Flatpak Repo]", "Url=https://dl.flathub.org/repo/", "GPGKey="):
        if expected not in remote_content:
            return fail(f"vendored Flathub definition must include {expected!r}")
    if "/usr/share/veyra/flathub.flatpakrepo" not in postinstall_content:
        return fail("post-install script must configure Flathub from the vendored definition")

    print("Veyra Next image source smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
