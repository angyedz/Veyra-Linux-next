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
    if "SplitArtifacts=partitions,uki,roothash,os-release,repart-definitions" not in output:
        return fail("the build must export root, verity and UKI artifacts for updates")
    for expected in (
        "Bootable=yes",
        "Bootloader=systemd-boot",
        "UnifiedKernelImages=unsigned",
        "Initrds=%O/initrd",
    ):
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
        "systemd-udev",
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
        "enable systemd-boot-check-no-failures.service",
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

    repart = ROOT / "mkosi.repart"
    root_partition = (repart / "11-root.conf").read_text(encoding="utf-8")
    verity_partition = (repart / "10-root-verity.conf").read_text(encoding="utf-8")
    for expected in ("Type=root", "Format=erofs", "Verity=data", "VerityMatchKey=root"):
        if expected not in root_partition:
            return fail(f"the active root partition must include {expected!r}")
    for expected in ("Type=root-verity", "Verity=hash", "VerityMatchKey=root"):
        if expected not in verity_partition:
            return fail(f"the active verity partition must include {expected!r}")
    for relative in (
        "20-empty-root-verity.conf",
        "21-empty-root.conf",
        "30-etc.conf",
        "40-var.conf",
        "50-home.conf",
    ):
        if not (repart / relative).is_file():
            return fail(f"missing A/B or persistent partition definition: {relative}")

    initrd_root = ROOT / "mkosi.images/initrd/mkosi.extra/usr/lib/systemd/system"
    for mount_name, label, where in (
        ("sysroot-etc.mount", "VEYRA_ETC", "/sysroot/etc"),
        ("sysroot-var.mount", "VEYRA_VAR", "/sysroot/var"),
    ):
        mount_unit = (initrd_root / mount_name).read_text(encoding="utf-8")
        for expected in (f"/dev/disk/by-partlabel/{label}", f"Where={where}", "Before=initrd-root-fs.target"):
            if expected not in mount_unit:
                return fail(f"initrd {mount_name} must include {expected!r}")
        link = initrd_root / "initrd-root-fs.target.wants" / mount_name
        if not link.is_symlink() or not link.exists():
            return fail(f"initrd must pull {mount_name} into initrd-root-fs.target")

    transfer_dir = ROOT / "mkosi.extra/usr/lib/sysupdate.d"
    transfer_names = sorted(path.name for path in transfer_dir.glob("*.transfer"))
    if transfer_names != ["10-root-verity.transfer", "20-root.transfer", "99-uki.transfer"]:
        return fail("systemd-sysupdate transfers must stage verity, root, then the UKI commit entry")
    for name in transfer_names:
        transfer = (transfer_dir / name).read_text(encoding="utf-8")
        for expected in ("Verify=yes", "ProtectVersion=%A", "Type=url-file"):
            if expected not in transfer:
                return fail(f"{name} must include {expected!r}")
    expected_sources = {
        "10-root-verity.transfer": "veyra-next_@v_@u.root-verity.raw.zst",
        "20-root.transfer": "veyra-next_@v_@u.root.raw.zst",
        "99-uki.transfer": "veyra-next_@v.efi",
    }
    for name, pattern in expected_sources.items():
        transfer = (transfer_dir / name).read_text(encoding="utf-8")
        if pattern not in transfer:
            return fail(f"{name} must match the generated release asset pattern {pattern!r}")
    for name in ("10-root-verity.transfer", "20-root.transfer"):
        transfer = (transfer_dir / name).read_text(encoding="utf-8")
        if "PartitionUUID=@u" not in transfer:
            return fail(f"{name} must preserve the split UUID required by the dm-verity root hash")
    uki_transfer = (transfer_dir / "99-uki.transfer").read_text(encoding="utf-8")
    for expected in ("TriesLeft=3", "TriesDone=0", "InstancesMax=2", "PathRelativeTo=boot"):
        if expected not in uki_transfer:
            return fail(f"UKI transfer must include {expected!r}")
    if not (ROOT / "mkosi.postoutput").is_file():
        return fail("mkosi must prepare versioned update assets after image output")
    if not (ROOT / "mkosi.configure").is_file():
        return fail("the initial UKI filename must use the same version as the root slot")
    if not (ROOT / "scripts/check-image.py").is_file():
        return fail("the built GPT layout must be checked in CI")
    boot_complete = ROOT / "mkosi.extra/usr/lib/systemd/system/boot-complete.target.d/veyra.conf"
    boot_complete_content = boot_complete.read_text(encoding="utf-8")
    for expected in ("Requires=graphical.target", "systemd-boot-check-no-failures.service", "After=graphical.target"):
        if expected not in boot_complete_content:
            return fail(f"boot assessment must wait for {expected!r}")

    logo = ROOT / "branding/veyra-logo.svg"
    installed_logo = ROOT / "mkosi.extra/usr/share/veyra/branding/veyra-logo.svg"
    if not logo.is_file() or not installed_logo.is_file():
        return fail("the canonical Veyra SVG must be included in the source and image")
    if logo.read_bytes() != installed_logo.read_bytes():
        return fail("the installed Veyra SVG must match the canonical source artwork")
    for relative in (
        "branding/fastfetch/config.jsonc",
        "branding/fastfetch/logo.txt",
        "branding/grub/theme.txt",
        "mkosi.extra/usr/share/veyra/fastfetch/config.jsonc",
        "mkosi.extra/usr/share/veyra/fastfetch/logo.txt",
    ):
        if not (ROOT / relative).is_file():
            return fail(f"missing inherited branding file: {relative}")

    print("Veyra Next image source smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
