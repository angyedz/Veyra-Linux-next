#!/usr/bin/env bash
# A/B installer *media*, not the installed A/B OS.
# Fedora RPM rootfs + dracut live + SquashFS + GRUB BIOS/UEFI ISO.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
build="${VEYRA_LIVE_BUILD_DIR:-$repo/build/native-live}"
root="$build/rootfs"
stage="$build/iso-tree"
output="$build/veyra-next-prototype-live.iso"
label=VEYRA_NEXT_LIVE
max_size=$((8 * 1024 * 1024 * 1024))

die() { printf 'Veyra Live: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || die "run in an isolated Fedora CI builder as root"
[[ ! -L "$build" && ! -L "$root" && ! -L "$stage" ]] || die "symlink build path refused"
for program in dnf5 mksquashfs grub2-mkrescue dracut chroot xorriso sha256sum; do
  command -v "$program" >/dev/null || die "missing builder tool: $program"
done
[[ -z "${VEYRA_LIVE_BUILD_DIR:-}" || "$build" == "$repo"/build/* ]] ||
  die "custom build directory must be under repository build/"
mkdir -p "$build"
[[ ! -e "$root" && ! -e "$stage" ]] || die "build already exists; remove disposable rootfs/iso-tree explicitly"
mkdir -p "$root" "$stage/boot/grub" "$stage/LiveOS"
echo "Composing Fedora 44 KDE Plasma Live rootfs (no Anaconda or Lorax)..."

# DNF validates Fedora repository GPG signatures. This rootfs is separate from
# the installed-system mkosi.conf package set and cannot alter it.
dnf5 -y --installroot="$root" --use-host-config --releasever=44 \
  --setopt=install_weak_deps=False install \
  fedora-release-kde-desktop kernel-core kernel-modules kernel-modules-extra \
  dracut dracut-live dracut-config-generic \
  systemd systemd-udev dbus NetworkManager NetworkManager-wifi \
  plasma-desktop plasma-workspace plasma-workspace-wayland plasma-systemsettings \
  sddm kwin-wayland breeze-icon-theme \
  plasma-nm plasma-pa dolphin konsole \
  pipewire pipewire-pulseaudio wireplumber \
  xorg-x11-server-Xwayland mesa-dri-drivers \
  calamares calamares-interactiveterminal kdialog \
  python3 dnf5 libdnf5 python3-libdnf5 \
  grub2-tools grub2-tools-extra grub2-pc grub2-pc-modules \
  grub2-efi-x64 grub2-efi-x64-modules grub2-tools-efi \
  systemd-boot-unsigned systemd-ukify \
  btrfs-progs cryptsetup dosfstools e2fsprogs erofs-utils \
  flatpak sudo polkit util-linux shadow-utils

# Never import production /etc or the host home directory into the Live system.
install -d "$root/etc/sddm.conf.d" "$root/etc/sudoers.d" \
  "$root/usr/share/veyra/installer/source/assets" \
  "$root/usr/share/veyra/installer/source/installer" \
  "$root/usr/share/veyra/branding" "$root/usr/share/applications" \
  "$root/usr/libexec" "$root/etc/calamares/modules" \
  "$root/etc/calamares/branding/veyra" "$root/etc/systemd/system"
cp -a "$repo/mkosi.conf" "$repo/mkosi.repart" "$repo/mkosi.extra" \
  "$root/usr/share/veyra/installer/source/"
cp -a "$repo/installer/dracut" "$root/usr/share/veyra/installer/source/installer/"
install -m 0755 "$repo/scripts/veyra-install.py" "$root/usr/libexec/veyra-installer"
install -m 0644 "$repo/installer/calamares/settings.conf" "$root/etc/calamares/settings.conf"
install -m 0644 "$repo/installer/calamares/modules/"*.conf "$root/etc/calamares/modules/"
install -m 0644 "$repo/installer/calamares/branding/veyra/branding.desc" \
  "$root/etc/calamares/branding/veyra/branding.desc"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/branding/veyra-logo.svg" \
  "$root/usr/share/veyra/branding/veyra-logo.svg"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/branding/veyra-logo.svg" \
  "$root/etc/calamares/branding/veyra/veyra-logo.svg"
if [[ -n "${VEYRA_RECOVERY_UKI:-}" ]]; then
  [[ -s "$VEYRA_RECOVERY_UKI" && ! -L "$VEYRA_RECOVERY_UKI" ]] ||
    die "invalid Recovery UKI input"
  install -m 0644 "$VEYRA_RECOVERY_UKI" \
    "$root/usr/share/veyra/installer/source/assets/veyra-recovery.efi"
fi

# The Live desktop is disposable; sudo permits only Calamares, not any shell.
chroot "$root" /usr/sbin/useradd --create-home --groups wheel --shell /bin/bash liveuser
chroot "$root" /usr/bin/passwd --lock liveuser
cat > "$root/etc/sudoers.d/veyra-live-calamares" <<'EOF'
liveuser ALL=(root) NOPASSWD: /usr/bin/calamares
EOF
chmod 0440 "$root/etc/sudoers.d/veyra-live-calamares"
cat > "$root/etc/sddm.conf.d/veyra-live.conf" <<'EOF'
[Autologin]
User=liveuser
Session=plasma.desktop
Relogin=false
EOF
cat > "$root/usr/share/applications/veyra-installer.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Install Veyra Next Prototype
Comment=Install to an explicitly selected unused disk
Exec=sudo -n /usr/bin/calamares
Icon=/usr/share/veyra/branding/veyra-logo.svg
Terminal=false
Categories=Qt;System;
EOF
echo 'Veyra Next Prototype Live' > "$root/etc/hostname"
install -d "$root/etc/systemd/system/multi-user.target.wants" "$root/etc/systemd/system/display-manager.service.wants"
ln -sfn /usr/lib/systemd/system/NetworkManager.service \
  "$root/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
ln -sfn /usr/lib/systemd/system/sddm.service \
  "$root/etc/systemd/system/display-manager.service"
ln -sfn /usr/lib/systemd/system/graphical.target \
  "$root/etc/systemd/system/default.target"
# The rootfs contains the dracut-live module and squashfs-tools from the
# builder; host-only detection is forbidden (CI host != booting VM).
mapfile -t kernels < <(find "$root/usr/lib/modules" -mindepth 1 -maxdepth 1 -type d -printf '%f\n')
(("${#kernels[@]}" == 1)) || die "expected one kernel, found ${#kernels[@]}"
version="${kernels[0]}"
linux="$root/usr/lib/modules/$version/vmlinuz"
[[ -s "$linux" ]] || die "kernel missing: $linux"
chroot "$root" /usr/bin/dracut --force --no-hostonly \
  --add "dmsquash-live" --kver "$version" "/boot/veyra-live-initrd.img"
[[ -s "$root/boot/veyra-live-initrd.img" ]] || die "dracut Live initrd missing"
cp "$linux" "$stage/boot/vmlinuz"
cp "$root/boot/veyra-live-initrd.img" "$stage/boot/initrd.img"
# Remove package caches and host-independent machine identifiers from the image.
rm -rf -- "$root/var/cache/dnf" "$root/var/cache/libdnf5"
: > "$root/etc/machine-id"
rm -f -- "$root/etc/hostname-"*
cat > "$stage/boot/grub/grub.cfg" <<'EOF'
set timeout=7
set default=0
menuentry 'Veyra Next Prototype Live (KDE Plasma)' {
  linux /boot/vmlinuz root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.image rd.live.overlay.overlayfs=1 quiet systemd.unit=graphical.target
  initrd /boot/initrd.img
}
menuentry 'Veyra Next Prototype Live (diagnostic console)' {
  linux /boot/vmlinuz root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.image rd.live.overlay.overlayfs=1 systemd.unit=multi-user.target console=ttyS0,115200n8
  initrd /boot/initrd.img
}
EOF
# dmsquash-live recognises direct SquashFS roots when /proc exists inside.
[[ -d "$root/proc" ]] || mkdir -p "$root/proc"
mksquashfs "$root" "$stage/LiveOS/squashfs.img" \
  -noappend -comp zstd -all-root -no-progress
grub2-mkrescue -o "$output" "$stage" -V "$label"
[[ -s "$output" ]] || die "GRUB did not generate a Live ISO"
size="$(stat -c %s "$output")"
((size <= max_size)) || die "ISO size $size exceeds 8 GiB"
xorriso -indev "$output" -report_el_torito as_mkisofs > "$build/eltorito.txt"
grep -Eq '(^|[[:space:]])-b[[:space:]]' "$build/eltorito.txt" || die "BIOS El Torito missing"
grep -Eq '(^|[[:space:]])-e[[:space:]]' "$build/eltorito.txt" || die "UEFI El Torito missing"
(cd "$build" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
echo "Live ISO: $output ($size bytes)"
