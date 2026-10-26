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
    config_values = section_values(config, "Config")
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
    for expected in (
        "Format=disk",
        "ElTorito=no",
        "Output=veyra-next",
        "OutputDirectory=mkosi.output",
        "ManifestFormat=json",
    ):
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
    for expected in (
        "ToolsTree=yes",
        "ToolsTreeDistribution=fedora",
        "ToolsTreeRelease=44",
    ):
        if expected not in build:
            return fail(f"{config.name} must contain {expected!r} in [Build]")
    if "Firmware=uefi" not in runtime:
        return fail("the unsigned prototype UKIs must boot with Secure Boot disabled in the test VM")
    if "KernelCommandLine=systemd.unit=graphical.target" not in content:
        return fail("the image must boot into the graphical desktop target")
    if "Dependencies=initrd" not in config_values:
        return fail("the bootable image must build its referenced initrd as an explicit dependency")

    oci_config = ROOT / "mkosi.images/veyra-oci-rootfs/mkosi.conf"
    oci_content = section_values(oci_config, "Content")
    if "Initrds=" not in oci_content:
        return fail("the OCI rootfs subimage must clear the installed image's inherited Initrds setting")

    shared_packages = ROOT / "mkosi.veyra-base.conf"
    if not shared_packages.is_file():
        return fail("the shared installed-image and OCI package definition is missing")
    shared_content = section_values(shared_packages, "Content")
    packages = {line.removeprefix("Packages=") for line in shared_content if line.startswith("Packages=")}
    if any(line.startswith("Packages=") for line in content):
        return fail("installed-image packages must come from the shared Veyra base definition")
    required_packages = {
        "kernel",
        "libdnf5",
        "systemd-udev",
        "NetworkManager",
        "firewalld",
        "flatpak",
        "python3-libdnf5",
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
        "e2fsprogs",
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
        "enable systemd-sysext.service",
        "enable systemd-boot-check-no-failures.service",
    ):
        if service not in preset_content:
            return fail(f"system preset must include {service!r}")

    vpkg = ROOT / "mkosi.extra/usr/bin/vpkg"
    if not vpkg.is_file() or not (vpkg.stat().st_mode & 0o111):
        return fail("vpkg must be installed as an executable in the base image")
    vpkg_content = vpkg.read_text(encoding="utf-8")
    for expected in (
        "from vpkg_libdnf import LibdnfError, Session",
        "package_session(",
        'commands.add_parser("stage"',
        "stage_user_apps(arguments.packages, assume_yes=arguments.yes)",
        "systemd-sysext",
        "SYSTEM_EXTENSION_LINK_DIR = Path(\"/etc/extensions\")",
        "USER_APP_SYSEXT_LINK = SYSTEM_EXTENSION_LINK_DIR / \"vpkg-apps\"",
        "check_user_app_base_conflicts",
        "Refusing non-Veyra RPM",
        "System files must be delivered by an A/B OS update",
        "check_extensions_active",
        "status", "--json=short",
    ):
        if expected not in vpkg_content:
            return fail(f"vpkg safety contract must include {expected!r}")
    if 'commands.add_parser("container"' in vpkg_content:
        policy = ROOT / "mkosi.extra/usr/share/veyra/vpkg/protected-core.json"
        try:
            policy_data = json.loads(policy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fail("container Vpkg must install a valid protected-core policy")
        if not isinstance(policy_data, dict) or not all(
            token in vpkg_content for token in ("container_protected_packages()", "validate_container_root(")
        ):
            return fail("container Vpkg must enforce protected-core policy and validate its Veyra root")
    elif "BLOCKED_PACKAGE_PREFIXES" not in vpkg_content:
        return fail("Vpkg must enforce its protected-package policy")
    backend = ROOT / "mkosi.extra/usr/lib/veyra/vpkg_libdnf.py"
    if not backend.is_file():
        return fail("vpkg must install its libdnf5 API adapter")
    backend_content = backend.read_text(encoding="utf-8")
    for expected in ("libdnf5.base.Base()", "libdnf5.base.Goal", "transaction.run()", '"noscripts", "notriggers"'):
        if expected not in backend_content:
            return fail(f"libdnf5 API adapter must include {expected!r}")
    if "dnf5" in packages or "dnf5-plugins" in packages:
        return fail("the installed image must not include the DNF5 CLI")
    if re.search(r"run\(\s*\[\s*['\"]dnf5['\"]", vpkg_content):
        return fail("vpkg must not spawn the DNF5 CLI")
    for channel in ("main", "testing"):
        repo_file = ROOT / f"mkosi.extra/etc/yum.repos.d/veyra-{channel}.repo"
        if not repo_file.is_file():
            return fail(f"missing Veyra {channel} repository configuration")
        repo_content = repo_file.read_text(encoding="utf-8")
        for expected in (
            f"[veyra-{channel}]",
            f"/repositories/{channel}/current/x86_64/",
            "enabled=0",
            "gpgcheck=1",
            "repo_gpgcheck=1",
            "gpgkey=file:///usr/share/veyra/RPM-GPG-KEY-VEYRA",
        ):
            if expected not in repo_content:
                return fail(f"Veyra {channel} repository must include {expected!r}")
        if "raw.githubusercontent.com" in repo_content or "github.io" in repo_content:
            return fail(f"Veyra {channel} RPMs must be resolved from GitHub Release snapshots")
    release_provider = ROOT / "mkosi.extra/usr/lib/veyra/veyra_repo.py"
    if not release_provider.is_file():
        return fail("the signed GitHub Releases repository provider must be installed")
    provider_content = release_provider.read_text(encoding="utf-8")
    for expected in (
        "veyra-rpm-repo-x86_64.tar.gz",
        "veyra-rpm-repo-x86_64.tar.gz.sha256",
        "repomd.xml.asc",
        "--verify",
        "_safe_extract",
        "rpm-vX.Y.Z-testing",
    ):
        if expected not in provider_content:
            return fail(f"the release provider must enforce {expected!r}")
    if "gnupg2" not in packages:
        return fail("the runtime image must include GnuPG for pinned repomd signature verification")
    rpmfusion = ROOT / "mkosi.extra/etc/yum.repos.d/rpmfusion.repo"
    if not rpmfusion.is_file():
        return fail("RPM Fusion free/nonfree repositories must be available to vpkg")
    for repository in (
        "rpmfusion-free",
        "rpmfusion-free-updates",
        "rpmfusion-nonfree",
        "rpmfusion-nonfree-updates",
    ):
        values = section_values(rpmfusion, repository)
        if not values:
            return fail(f"missing RPM Fusion repository section: {repository}")
        for expected in ("enabled=1", "gpgcheck=1", "repo_gpgcheck=0"):
            if expected not in values:
                return fail(f"RPM Fusion {repository} must include {expected!r}")
        license_channel = "nonfree" if "nonfree" in repository else "free"
        expected_key = f"RPM-GPG-KEY-rpmfusion-{license_channel}-fedora-2020"
        if not any(expected_key in value for value in values):
            return fail(f"RPM Fusion {repository} must use its pinned public key")
        key_file = ROOT / "mkosi.extra/usr/share/pki/rpm-gpg" / expected_key
        if not key_file.is_file() or "BEGIN PGP PUBLIC KEY BLOCK" not in key_file.read_text(encoding="ascii"):
            return fail(f"missing embedded RPM Fusion signing key: {expected_key}")
    for expected in (
        "fedora",
        "updates",
        "rpmfusion-free",
        "rpmfusion-free-updates",
        "rpmfusion-nonfree",
        "rpmfusion-nonfree-updates",
    ):
        if expected not in vpkg_content:
            return fail(f"vpkg must select the upstream package repository {expected!r}")
    sync_service = ROOT / "mkosi.extra/usr/lib/systemd/system/veyra-vpkg-sync.service"
    sysext_dropin = ROOT / "mkosi.extra/usr/lib/systemd/system/systemd-sysext.service.d/veyra-vpkg.conf"
    if not sync_service.is_file() or not sysext_dropin.is_file():
        return fail("systemd-sysext must sync Veyra package links before activation")
    if "ConditionPathExists=/var/lib/veyra/vpkg/root" in sync_service.read_text(encoding="utf-8"):
        return fail("Veyra app-layer links must synchronize at boot even when no Veyra RPMs are installed")
    if "Before=systemd-sysext.service" not in sync_service.read_text(encoding="utf-8"):
        return fail("Veyra package link synchronization must run before systemd-sysext")
    if "After=veyra-vpkg-sync.service" not in sysext_dropin.read_text(encoding="utf-8"):
        return fail("systemd-sysext must wait for Veyra extension links to synchronize")
    stage_test_service = ROOT / "tests/fixtures/vpkg-image/veyra-vpkg-stage-recovery.service"
    stage_test_script = ROOT / "tests/fixtures/vpkg-image/veyra-vpkg-stage-recovery"
    if not stage_test_service.is_file() or not stage_test_script.is_file():
        return fail("the QEMU fixture must cover staged-generation rollback before graphical.target")
    if "Before=graphical.target" not in stage_test_service.read_text(encoding="utf-8"):
        return fail("the staged rollback QEMU helper must run before the graphical target")
    for channel in ("main", "testing"):
        package_list = ROOT / f"packages/{channel}/packages.list"
        if not package_list.is_file():
            return fail(f"missing package-channel manifest: {package_list.relative_to(ROOT)}")
        for line in package_list.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if name and not name.startswith("#") and not name.startswith("veyra-"):
                return fail(f"Fedora/base packages must not be published into Veyra channels: {name}")

    postinstall = ROOT / "mkosi.postinst.chroot"
    postinstall_content = postinstall.read_text(encoding="utf-8")
    if "flatpak remote-add --system --if-not-exists flathub" not in postinstall_content:
        return fail("post-install script must configure Flathub for all users")
    sysext_level_file = ROOT / "mkosi.extra/usr/share/veyra/sysext-level"
    sysext_level = sysext_level_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", sysext_level):
        return fail("Veyra system-extension compatibility level must be a safe identifier")
    if "/usr/share/veyra/sysext-level" not in postinstall_content or "SYSEXT_LEVEL=" not in postinstall_content:
        return fail("the base OS release must expose the Veyra system-extension compatibility level")
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
        if "ReadOnly=1" not in transfer:
            return fail(f"{name} must mark updated root and verity partitions read-only")
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
    if not (ROOT / "scripts/test-ab-boot.py").is_file():
        return fail("CI must boot a staged A/B update and exercise boot-count rollback")
    update_client = ROOT / "mkosi.extra/usr/bin/veyra-update"
    if not update_client.is_file() or not (update_client.stat().st_mode & 0o111):
        return fail("the image must install the veyra-update command")
    update_client_content = update_client.read_text(encoding="utf-8")
    for expected in ("check-new", "--no-pager update", "--no-pager pending", "systemd-sysupdate reboot"):
        if expected not in update_client_content:
            return fail(f"veyra-update must expose {expected!r}")
    boot_complete = ROOT / "mkosi.extra/usr/lib/systemd/system/boot-complete.target.d/veyra.conf"
    boot_complete_content = boot_complete.read_text(encoding="utf-8")
    for expected in (
        "Requires=graphical.target",
        "systemd-boot-check-no-failures.service",
        "veyra-vpkg-check.service",
        "After=graphical.target",
    ):
        if expected not in boot_complete_content:
            return fail(f"boot assessment must wait for {expected!r}")
    extension_check = ROOT / "mkosi.extra/usr/lib/systemd/system/veyra-vpkg-check.service"
    if not extension_check.is_file() or "systemd-sysext.service" not in extension_check.read_text(encoding="utf-8"):
        return fail("boot assessment must verify Veyra extensions after systemd-sysext activation")
    boot_failure_check = ROOT / "mkosi.extra/usr/lib/systemd/system/systemd-boot-check-no-failures.service.d/veyra-vpkg.conf"
    if not boot_failure_check.is_file() or "After=veyra-vpkg-check.service" not in boot_failure_check.read_text(encoding="utf-8"):
        return fail("boot blessing must wait for the Veyra extension health check")

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
