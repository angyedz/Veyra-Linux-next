#!/usr/bin/env python3
"""Check the source contract for the Veyra Next Fedora image."""

from __future__ import annotations

import json
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
    runtime = section_values(config, "Runtime")

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
    if "Firmware=uefi" not in runtime:
        return fail("the unsigned prototype UKIs must boot with Secure Boot disabled in the test VM")
    if "KernelCommandLine=systemd.unit=graphical.target" not in content:
        return fail("the image must boot into the graphical desktop target")

    packages = {line.removeprefix("Packages=") for line in content if line.startswith("Packages=")}
    required_packages = {
        "kernel",
        "NetworkManager",
        "firewalld",
        "flatpak",
        "plasma-setup",
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
        "enable plasma-setup.service",
        "enable plasmalogin.service",
    ):
        if service not in preset_content:
            return fail(f"system preset must include {service!r}")

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

    logo = ROOT / "branding/veyra-logo.svg"
    installed_logo = ROOT / "mkosi.extra/usr/share/veyra/branding/veyra-logo.svg"
    if not logo.is_file() or not installed_logo.is_file():
        return fail("the canonical Veyra SVG must be included in the source and image")
    if logo.read_bytes() != installed_logo.read_bytes():
        return fail("the installed Veyra SVG must match the canonical source artwork")
    for relative in (
        "branding/fastfetch/config.jsonc",
        "branding/fastfetch/logo.txt",
        "branding/fetch/logo.txt",
        "branding/neofetch/logo.txt",
        "branding/screenfetch/logo.sh",
        "scripts/generate-fetch-logos.py",
        "branding/grub/theme.txt",
        "mkosi.extra/usr/share/veyra/fastfetch/config.jsonc",
        "mkosi.extra/usr/share/veyra/fastfetch/logo.txt",
        "mkosi.extra/usr/share/veyra/branding/fetch-logo.txt",
        "mkosi.extra/usr/share/veyra/neofetch/logo.txt",
        "mkosi.extra/usr/share/veyra/screenfetch/logo.sh",
        "mkosi.extra/etc/xdg/fastfetch/config.jsonc",
        "mkosi.extra/etc/skel/.config/neofetch/config.conf",
        "mkosi.extra/etc/skel/.config/hyfetch.json",
        "mkosi.extra/etc/profile.d/veyra-fetch.sh",
        "mkosi.extra/usr/lib/veyra/os-release",
    ):
        if not (ROOT / relative).is_file():
            return fail(f"missing inherited branding file: {relative}")

    fastfetch_config = json.loads((ROOT / "branding/fastfetch/config.jsonc").read_text(encoding="utf-8"))
    if set(fastfetch_config) != {"$schema", "logo"}:
        return fail("Fastfetch branding must change only its logo and retain its default information layout")
    installed_fastfetch_config = ROOT / "mkosi.extra/usr/share/veyra/fastfetch/config.jsonc"
    xdg_fastfetch_config = ROOT / "mkosi.extra/etc/xdg/fastfetch/config.jsonc"
    if fastfetch_config != json.loads(installed_fastfetch_config.read_text(encoding="utf-8")):
        return fail("the installed Fastfetch config must match the source config")
    if fastfetch_config != json.loads(xdg_fastfetch_config.read_text(encoding="utf-8")):
        return fail("the system Fastfetch config must match the source config")
    if fastfetch_config["logo"]["source"] != "/usr/share/veyra/fastfetch/logo.txt":
        return fail("Fastfetch must point at the installed Veyra ASCII logo")

    matching_assets = (
        ("branding/fetch/logo.txt", "mkosi.extra/usr/share/veyra/branding/fetch-logo.txt"),
        ("branding/fastfetch/logo.txt", "mkosi.extra/usr/share/veyra/fastfetch/logo.txt"),
        ("branding/neofetch/logo.txt", "mkosi.extra/usr/share/veyra/neofetch/logo.txt"),
        ("branding/screenfetch/logo.sh", "mkosi.extra/usr/share/veyra/screenfetch/logo.sh"),
    )
    for source, installed in matching_assets:
        if (ROOT / source).read_bytes() != (ROOT / installed).read_bytes():
            return fail(f"installed ASCII artwork must match {source}")

    neofetch_config = (ROOT / "mkosi.extra/etc/skel/.config/neofetch/config.conf").read_text(encoding="utf-8")
    if 'image_source="/usr/share/veyra/neofetch/logo.txt"' not in neofetch_config:
        return fail("new user profiles must point Neofetch at the Veyra logo")
    if "print_info" in neofetch_config or "info \"" in neofetch_config:
        return fail("Neofetch branding must leave its default system information untouched")

    hyfetch_config = json.loads((ROOT / "mkosi.extra/etc/skel/.config/hyfetch.json").read_text(encoding="utf-8"))
    if hyfetch_config.get("custom_ascii_path") != "/usr/share/veyra/neofetch/logo.txt":
        return fail("new user profiles must point HyFetch at the shared Veyra logo")
    if hyfetch_config.get("backend") != "fastfetch":
        return fail("HyFetch must use Fastfetch's default information layout")
    if hyfetch_config.get("custom_presets", {}).get("veyra") != ["#55B8FF", "#EAF8FF"]:
        return fail("HyFetch must color only the shared logo with Veyra's SVG colors")

    screenfetch_integration = (ROOT / "mkosi.extra/etc/profile.d/veyra-fetch.sh").read_text(encoding="utf-8")
    if "screenfetch -a /usr/share/veyra/screenfetch/logo.sh" not in screenfetch_integration:
        return fail("Screenfetch should use the shared Veyra logo by default")

    release = (ROOT / "mkosi.extra/usr/lib/veyra/os-release").read_text(encoding="utf-8")
    for expected in ('NAME="Veyra Next"', "ID=veyra", "ID_LIKE=fedora", "VERSION_ID=44"):
        if expected not in release:
            return fail(f"Veyra os-release must preserve {expected!r}")
    release_link = ROOT / "mkosi.extra/etc/os-release"
    if not release_link.is_symlink() or release_link.readlink().as_posix() != "../usr/lib/veyra/os-release":
        return fail("/etc/os-release must resolve to Veyra's Fedora-compatible release metadata")
    if "Packages=fastfetch" not in content:
        return fail("Fastfetch must be available in the default image")

    print("Veyra Next image source smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
