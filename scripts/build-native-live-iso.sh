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
  systemd systemd-udev systemd-pam dbus dbus-daemon NetworkManager NetworkManager-wifi \
  plasma-desktop plasma-workspace plasma-workspace-wayland plasma-systemsettings \
  sddm sddm-x11 kwin-wayland breeze-icon-theme \
  plasma-nm plasma-pa dolphin konsole konsole-part kate \
  plasma-discover plasma-discover-flatpak \
  xdg-desktop-portal xdg-desktop-portal-kde \
  pipewire pipewire-pulseaudio wireplumber \
  xorg-x11-server-Xwayland mesa-dri-drivers \
  calamares calamares-interactiveterminal kdialog \
  python3 dnf5 libdnf5 python3-libdnf5 rpm squashfs-tools \
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
  "$root/usr/lib/veyra" "$root/etc/veyra" "$root/etc/yum.repos.d" \
  "$root/usr/share/pki/rpm-gpg" \
  "$root/etc/calamares/branding/veyra" "$root/etc/systemd/system"
cp -a "$repo/mkosi.conf" "$repo/mkosi.repart" "$repo/mkosi.extra" \
  "$root/usr/share/veyra/installer/source/"
cp -a "$repo/installer/dracut" "$root/usr/share/veyra/installer/source/installer/"
install -m 0755 "$repo/scripts/veyra-install.py" "$root/usr/libexec/veyra-installer"
install -m 0644 "$repo/installer/calamares/settings.conf" "$root/etc/calamares/settings.conf"
install -m 0644 "$repo/installer/calamares/modules/"*.conf "$root/etc/calamares/modules/"
# The Calamares interactive terminal is a separate Fedora KPart package.
# A plain 'konsole' installation does not guarantee this plugin is present.
[[ -s "$root/usr/lib64/qt6/plugins/kf6/parts/konsolepart.so" ]] ||
  die "Calamares terminal plugin missing: install Fedora konsole-part"
[[ -x "$root/usr/bin/calamares" ]] ||
  die "Calamares GUI executable missing from Live rootfs"
install -m 0644 "$repo/installer/calamares/branding/veyra/branding.desc" \
  "$root/etc/calamares/branding/veyra/branding.desc"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/branding/veyra-logo.svg" \
  "$root/usr/share/veyra/branding/veyra-logo.svg"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/branding/veyra-logo.svg" \
  "$root/etc/calamares/branding/veyra/veyra-logo.svg"
# Ship the same native vpkg policy/API implementation as the installed
# system, but do not copy veyra-update, OTA transfer definitions or boot-bless
# units. Live packages operate in a disposable overlay; no A/B partitions.
install -m 0755 "$repo/mkosi.extra/usr/bin/vpkg" "$root/usr/bin/vpkg"
install -m 0644 "$repo/mkosi.extra/usr/lib/veyra/vpkg_libdnf.py" \
  "$root/usr/lib/veyra/vpkg_libdnf.py"
install -m 0644 "$repo/mkosi.extra/usr/lib/veyra/veyra_repo.py" \
  "$root/usr/lib/veyra/veyra_repo.py"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/sysext-level" \
  "$root/usr/share/veyra/sysext-level"
install -m 0644 "$repo/mkosi.extra/usr/share/veyra/flathub.flatpakrepo" \
  "$root/usr/share/veyra/flathub.flatpakrepo"
install -m 0644 "$repo/mkosi.extra/etc/yum.repos.d/"*.repo \
  "$root/etc/yum.repos.d/"
install -m 0644 "$repo/mkosi.extra/usr/share/pki/rpm-gpg/"* \
  "$root/usr/share/pki/rpm-gpg/"
# If a production Veyra signing key is not provisioned, vpkg automatically
# excludes those channels and continues to offer signed Fedora apps.
if [[ -s "$repo/mkosi.extra/usr/share/veyra/RPM-GPG-KEY-VEYRA" ]]; then
  install -m 0644 "$repo/mkosi.extra/usr/share/veyra/RPM-GPG-KEY-VEYRA" \
    "$root/usr/share/veyra/RPM-GPG-KEY-VEYRA"
fi
# The system extension ABI must match the vpkg release metadata.
os_release="$root/usr/lib/os-release"
[[ -f "$os_release" && ! -L "$os_release" ]] || die "missing Fedora os-release for vpkg"
sysext_level="$(< "$root/usr/share/veyra/sysext-level")"
[[ "$sysext_level" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "invalid vpkg sysext level"
if grep -q '^SYSEXT_LEVEL=' "$os_release"; then
  sed -i "s/^SYSEXT_LEVEL=.*/SYSEXT_LEVEL=$sysext_level/" "$os_release"
else
  printf 'SYSEXT_LEVEL=%s\n' "$sysext_level" >> "$os_release"
fi
# Both the marker and kernel's root=live:CDLABEL are required by vpkg to use
# ephemeral identity instead of an installed, verity-protected A/B roothash.
printf 'veyra-next-native-live-iso\n' > "$root/etc/veyra/live-mode"
chmod 0644 "$root/etc/veyra/live-mode"
# Mask system OTA activation in Live only. Installed images are unaffected.
for unit in systemd-sysupdate.service systemd-sysupdate.timer \
  systemd-sysupdate-reboot.service systemd-sysupdate-reboot.timer; do
  ln -sfn /dev/null "$root/etc/systemd/system/$unit"
done
# A package-manager and helper sanity check runs inside the composed root.
chroot "$root" /usr/bin/vpkg --help >/dev/null
[[ ! -e "$root/usr/bin/veyra-update" ]] || die "Live system must not ship installed OTA updater"
[[ ! -d "$root/usr/lib/sysupdate.d" ]] || die "Live system must not contain A/B transfer rules"
if [[ -n "${VEYRA_RECOVERY_UKI:-}" ]]; then
  [[ -s "$VEYRA_RECOVERY_UKI" && ! -L "$VEYRA_RECOVERY_UKI" ]] ||
    die "invalid Recovery UKI input"
  install -m 0644 "$VEYRA_RECOVERY_UKI" \
    "$root/usr/share/veyra/installer/source/assets/veyra-recovery.efi"
fi

# Exit 127 in SDDM Wayland autologin is commonly caused by a missing
# dbus-run-session: plasma-dbus-run-session-if-needed executes that binary.
# systemd-pam supplies pam_systemd.so to create proper logind user sessions.
for binary in \
  /usr/bin/dbus-run-session \
  /usr/bin/dbus-daemon \
  /usr/bin/startplasma-wayland \
  /usr/libexec/plasma-dbus-run-session-if-needed; do
  [[ -x "$root$binary" ]] || die "required Plasma session executable missing: $binary"
done
[[ -s "$root/usr/lib64/security/pam_systemd.so" ]] ||
  die "Fedora systemd-pam is missing: pam_systemd.so"
# Check that every session binary starts with a resolved interpreter/loader.
chroot "$root" /usr/bin/dbus-run-session --version >/dev/null ||
  die "dbus-run-session is not executable inside Live rootfs"
# KDE 6.7 can select the unaccelerated virtio 2D render node as a
# separate compositor GPU. Restrict the workaround to QEMU/KVM Live guests
# with a virtio_gpu DRM card; hardware installs retain normal GPU discovery.
# systemd --user environment generators run before Plasma user services,
# unlike /etc/environment which does not reliably reach kwin's user unit.
install -d "$root/usr/lib/systemd/user-environment-generators"
cat > "$root/usr/lib/systemd/user-environment-generators/90-veyra-live-virtio-render" <<'EOF'
#!/bin/sh
[ -f /etc/veyra/live-mode ] || exit 0
[ "$(cat /etc/veyra/live-mode)" = "veyra-next-native-live-iso" ] || exit 0
case "$(systemd-detect-virt --vm 2>/dev/null)" in
  qemu|kvm) ;;
  *) exit 0 ;;
esac
for driver in /sys/class/drm/card*/device/driver; do
  [ -L "$driver" ] || continue
  [ "$(basename "$(readlink -f "$driver")")" = "virtio_gpu" ] || continue
  printf 'KWIN_RENDER_NODES=\n'
  exit 0
done
exit 0
EOF
chmod 0755 "$root/usr/lib/systemd/user-environment-generators/90-veyra-live-virtio-render"
# This root password is ONLY for a throwaway Live ISO, never for installed
# Veyra. It is public, deliberately weak for prototype recovery, and must
# never be shipped as a production or internet-exposed credential.
printf 'root:veyra\n' | chroot "$root" /usr/sbin/chpasswd
# Do not make the Live root account remotely accessible if sshd is ever added.
install -d "$root/etc/ssh/sshd_config.d"
cat > "$root/etc/ssh/sshd_config.d/90-veyra-live-no-root-login.conf" <<'EOF'
PermitRootLogin no
PasswordAuthentication no
EOF
# Live desktop account has no password and uses SDDM autologin.
chroot "$root" /usr/sbin/useradd --create-home --shell /bin/bash liveuser
chroot "$root" /usr/bin/passwd --lock liveuser
# Preserve the Live user's UID/GID in the SquashFS image. Rewriting all
# ownership to root makes $HOME unwritable and breaks Plasma autologin.
live_uid="$(chroot "$root" /usr/bin/id -u liveuser)"
live_gid="$(chroot "$root" /usr/bin/id -g liveuser)"
[[ "$(stat -c '%u:%g' "$root/home/liveuser")" == "$live_uid:$live_gid" ]] ||
  die "Live home owner mismatch; KDE cannot start with an unwritable home"
# Run Calamares GUI as the ordinary Wayland user, NOT via sudo.
# Only its terminal-based installer backend can acquire root. The blank
# argument rule is intentional: other --target/--confirm-device paths stay
# disallowed, and the backend still requires Recovery UKI preflight.
cat > "$root/etc/sudoers.d/veyra-live-calamares" <<'EOF'
liveuser ALL=(root) NOPASSWD: /usr/libexec/veyra-installer "", /usr/libexec/veyra-installer --preflight
EOF
chmod 0440 "$root/etc/sudoers.d/veyra-live-calamares"
chroot "$root" /usr/sbin/visudo -cf /etc/sudoers.d/veyra-live-calamares ||
  die "invalid restricted Calamares backend sudoers policy"
# Allow only signed Fedora/RPM Fusion application names via vpkg install,
# WITHOUT making the Live desktop a sudo-capable administrator. In particular,
# no shell, arbitrary paths, options or OTA/installer commands are whitelisted.
# sudoers matches the *complete* argument string with this anchored regex.
cat > "$root/etc/sudoers.d/veyra-live-vpkg-install" <<'EOF'
liveuser ALL=(root) NOPASSWD: /usr/bin/vpkg ^install[[:space:]]+[A-Za-z0-9][A-Za-z0-9+_.-]*([[:space:]]+[A-Za-z0-9][A-Za-z0-9+_.-]*)*$
EOF
chmod 0440 "$root/etc/sudoers.d/veyra-live-vpkg-install"
chroot "$root" /usr/sbin/visudo -cf /etc/sudoers.d/veyra-live-vpkg-install ||
  die "invalid Live-only vpkg sudoers policy"
# /usr/local/bin precedes /usr/bin in a normal interactive Fedora shell.
# Only 'vpkg install NAME...' crosses the privilege boundary; all other
# commands go directly to vpkg, retaining their normal policy.
install -d "$root/usr/local/bin"
cat > "$root/usr/local/bin/vpkg" <<'EOF'
#!/usr/bin/bash
if [[ "${1:-}" == install && "${EUID}" -ne 0 ]]; then
  shift
  exec /usr/bin/sudo -n /usr/bin/vpkg install "$@"
fi
exec /usr/bin/vpkg "$@"
EOF
chmod 0755 "$root/usr/local/bin/vpkg"
# Prevent a lock-screen password prompt for a deliberately passwordless user.
install -d "$root/home/liveuser/.config"
cat > "$root/home/liveuser/.config/kscreenlockerrc" <<'EOF'
[Daemon]
Autolock=false
LockOnResume=false
RequirePassword=false
EOF
chroot "$root" /usr/bin/chown -R liveuser:liveuser /home/liveuser/.config
# Live media is a test environment: do not auto-suspend or hibernate.
# Installed A/B system and its power settings stay untouched.
for unit in sleep.target suspend.target hibernate.target \
  hybrid-sleep.target suspend-then-hibernate.target; do
  ln -sfn /dev/null "$root/etc/systemd/system/$unit"
done
install -d "$root/etc/systemd/logind.conf.d"
cat > "$root/etc/systemd/logind.conf.d/90-veyra-live.conf" <<'EOF'
[Login]
IdleAction=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
EOF
cat > "$root/etc/sddm.conf.d/veyra-live.conf" <<'EOF'
# Use X11 only for the SDDM greeter: some software-rendered QEMU
# adapters crash the greeter's KWin Wayland compositor.
# The selected user session remains KDE Plasma Wayland.
[General]
DisplayServer=x11

[Autologin]
User=liveuser
Session=plasma.desktop
Relogin=false
EOF
# Keep Qt/KDE's authenticated Wayland display and XDG_RUNTIME_DIR by
# starting the frontend as the unprivileged, already logged-in Live user.
# Calamares is a UI wrapper: the terminal module launches a separate backend.
cat > "$root/usr/libexec/veyra-live-launch-calamares" <<'EOF'
#!/usr/bin/bash
set -u
if [[ "${EUID}" -eq 0 ]]; then
  echo "Run the Veyra Live Calamares frontend as liveuser, not root." >&2
  exit 1
fi
if [[ ! -f /etc/veyra/live-mode ]]; then
  echo "The Veyra Live installer is available only in the Live session." >&2
  exit 1
fi
log_dir="${XDG_CACHE_HOME:-$HOME/.cache}/veyra-live"
mkdir -p "$log_dir" || exit 1
log_file="$log_dir/calamares.log"
if /usr/bin/calamares -c /etc/calamares > "$log_file" 2>&1; then
  exit 0
else
  status=$?
  printf 'Calamares failed (exit %d); see %s\n' "$status" "$log_file" >&2
  if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    /usr/bin/kdialog --title "Veyra Next Installer" --error \
      "Calamares could not start. Log: $log_file" || true
  fi
  exit "$status"
fi
EOF
chmod 0755 "$root/usr/libexec/veyra-live-launch-calamares"
cat > "$root/usr/libexec/veyra-live-calamares-session" <<'EOF'
#!/usr/bin/bash
# Runs inside Calamares's interactive KonsolePart as liveuser. Checking the
# immutable Recovery UKI comes BEFORE disk selection or any destructive work.
printf '\nVeyra Next experimental installer\n'
if ! /usr/bin/sudo -n /usr/libexec/veyra-installer --preflight; then
  printf '\nInstallation is disabled: required Recovery UKI or installer assets are missing.\n'
  printf 'No disk has been modified. This prototype is for Live testing.\n'
  exit 1
fi
printf '\nPreflight passed. Disk selection still requires explicit ERASE confirmation.\n'
/usr/bin/sudo -n /usr/libexec/veyra-installer
EOF
chmod 0755 "$root/usr/libexec/veyra-live-calamares-session"
# Calamares' GUI lives in the application menu and on the Plasma desktop.
cat > "$root/usr/share/applications/veyra-installer.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Install Veyra Next
GenericName=Veyra Next Installer (Experimental)
Comment=Install Veyra Next to an unused disk (requires Recovery UKI)
Exec=/usr/libexec/veyra-live-launch-calamares
TryExec=/usr/bin/calamares
Icon=/usr/share/veyra/branding/veyra-logo.svg
Terminal=false
StartupNotify=true
Categories=System;Settings;
EOF
install -d "$root/home/liveuser/Desktop"
install -m 0755 "$root/usr/share/applications/veyra-installer.desktop" \
  "$root/home/liveuser/Desktop/Install Veyra Next.desktop"
chroot "$root" /usr/bin/chown -R liveuser:liveuser /home/liveuser/Desktop
echo 'Veyra Next Prototype Live' > "$root/etc/hostname"
# QEMU-only diagnostics: recover session errors even if KWin leaves a black
# screen. Never enable this on real hardware or in the installed system.
cat > "$root/usr/libexec/veyra-live-graphics-diagnostics" <<'EOF'
#!/usr/bin/bash
set -u
sleep 18
{
  echo '=== VEYRA LIVE GRAPHICS DIAGNOSTICS BEGIN ==='
  journalctl -b -u sddm.service --no-pager -n 80 --output=short-monotonic || true
  journalctl -b _UID="$(id -u liveuser)" --no-pager -n 100 --output=short-monotonic || true
  for file in /home/liveuser/.local/share/sddm/wayland-session.log \
              /home/liveuser/.local/share/sddm/x11-session.log \
              /home/liveuser/.xsession-errors; do
    if [[ -f "$file" && ! -L "$file" ]]; then
      printf '=== %s ===\n' "$file"
      tail -n 120 "$file" || true
    fi
  done
  if command -v coredumpctl >/dev/null; then
    coredumpctl --no-pager list 2>&1 | tail -n 40 || true
  fi
  echo '=== VEYRA LIVE GRAPHICS DIAGNOSTICS END ==='
} > /dev/ttyS0 2>&1
EOF
chmod 0755 "$root/usr/libexec/veyra-live-graphics-diagnostics"
cat > "$root/etc/systemd/system/veyra-live-graphics-diagnostics.service" <<'EOF'
[Unit]
Description=Capture failed Plasma login diagnostics in disposable QEMU
ConditionVirtualization=qemu
After=sddm.service
[Service]
Type=oneshot
ExecStart=/usr/libexec/veyra-live-graphics-diagnostics
[Install]
WantedBy=graphical.target
EOF
install -d "$root/etc/systemd/system/graphical.target.wants"
ln -sfn ../veyra-live-graphics-diagnostics.service \
  "$root/etc/systemd/system/graphical.target.wants/veyra-live-graphics-diagnostics.service"
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
  linux /boot/vmlinuz root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.image rd.live.overlay.overlayfs=1 rd.debug systemd.unit=graphical.target console=tty0 console=ttyS0,115200n8 loglevel=7
  initrd /boot/initrd.img
}
menuentry 'Veyra Next Prototype Live (diagnostic console)' {
  linux /boot/vmlinuz root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.image rd.live.overlay.overlayfs=1 rd.debug systemd.unit=multi-user.target console=tty0 console=ttyS0,115200n8 loglevel=7
  initrd /boot/initrd.img
}
EOF
# dmsquash-live recognises direct SquashFS roots when /proc exists inside.
[[ -d "$root/proc" ]] || mkdir -p "$root/proc"
mksquashfs "$root" "$stage/LiveOS/squashfs.img" \
  -noappend -comp zstd -no-progress
grub2-mkrescue -o "$output" "$stage" -V "$label"
[[ -s "$output" ]] || die "GRUB did not generate a Live ISO"
size="$(stat -c %s "$output")"
((size <= max_size)) || die "ISO size $size exceeds 8 GiB"
xorriso -indev "$output" -report_el_torito as_mkisofs > "$build/eltorito.txt"
grep -Eq '(^|[[:space:]])-b[[:space:]]' "$build/eltorito.txt" || die "BIOS El Torito missing"
grep -Eq '(^|[[:space:]])-e[[:space:]]' "$build/eltorito.txt" || die "UEFI El Torito missing"
(cd "$build" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
echo "Live ISO: $output ($size bytes)"
