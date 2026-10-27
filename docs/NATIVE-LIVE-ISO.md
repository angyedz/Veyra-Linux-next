# Native Veyra Live ISO experiment (Anaconda-free)

This is a **new experimental pipeline**. It is independent of the
previous Lorax/Anaconda pipeline and does not change the installed system
or its mkosi A/B layout. It uses Fedora 44 DNF5 RPM composition into an
isolated installroot, builds a dracut Live initramfs, SquashFS root,
and a GRUB rescue ISO with BIOS + UEFI El Torito entries.

CI artifact: `veyra-next-native-live-iso`; GitHub Actions workflow
`Veyra Anaconda-free Live ISO experiment`. Max size 8 GiB.

Calamares is the user-facing installer. Its existing terminal module runs
`/usr/libexec/veyra-installer`. The installed system's package manifest,
repart definitions, and Dracut module are copied into the live source
directory. Only the Live user is granted passwordless permission for the
Calamares binary. Anaconda is neither installed nor invoked by this pipeline.

**Important initial limitation:** the recovery UKI input is optional for
building the first *bootable media* prototype; without
`VEYRA_RECOVERY_UKI=/path/to/veyra-recovery.efi` the installer's
mandatory preflight must reject destructive installation. Do not call a
Live media ISO with no bundled Recovery UKI an installable release.

The GRUB boot options use Dracut's `dmsquash-live` format:
`root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.overlay.overlayfs=1`;
rootfs is a direct SquashFS (its internal root includes `/proc`).
Verify boot in both firmware modes and check that the KDE Wayland
session / Calamares opens before promoting this prototype.

The source tests validate configuration, not firmware boot. A built ISO
with both El Torito records is **not** itself proof that the kernel,
initrd, writable overlay, desktop, or installer work correctly.
No automatic merge to main and no claim of a usable Live ISO until
CI produces the artifact and QEMU boots both modes.

## Live session package and update policy

The experimental ISO ships the native Fedora 44 `vpkg` implementation,
the Python libdnf5 adapter, RPM Fusion keys/repository definitions, Discover,
Flatpak support, and the Calamares installer. Fedora and RPM Fusion packages
use their normal signature verification. The production Veyra RPM channel is
unavailable until the trusted release signing key is provisioned; no signature
checks are bypassed for Live media.

The installed system's updater (`veyra-update`), sysupdate transfer
definitions, OTA boot-blessing services, and persistent A/B machinery are
**not** installed in the Live runtime. systemd-sysupdate services/timers are
masked in Live only. `vpkg` requires both the read-only Live-mode marker and
the kernel's `root=live:CDLABEL=VEYRA_NEXT_LIVE` token to derive an ephemeral
per-boot identity; a marker alone cannot bypass installed dm-verity/UKI
identity checks. `vpkg stage` and OTA-binding subcommands are refused.
Installing a normal application creates a temporary app extension in the
writable Live overlay; it is lost on reboot. Installed Veyra Next retains
its own separate A/B/Recovery update logic.

The builder must preserve Unix ownership inside SquashFS; never enable
`mksquashfs -all-root`, which would make the autologged-in user's home
unwritable and can prevent Plasma from starting.

To download the **experimental** ISO, open the successful/latest
`Veyra Anaconda-free Live ISO experiment` Actions run, expand Artifacts,
and select `veyra-next-native-live-iso`. Its ZIP contains the ISO and the
SHA-256 file. It is not a production release. Without an included Recovery UKI,
Calamares preflight must stop destructive installation. Test Live desktop only,
using a throwaway VM or expendable boot media, with Secure Boot disabled.

## Current KDE diagnostic status

The ISO may reach `graphical.target` and successfully authenticate `liveuser`
without displaying Plasma; a successful QEMU kernel/init gate does **not**
prove graphical desktop readiness. BIOS and UEFI 120-second screenshots from
run 40507253123 were black. The following test iteration emits SDDM journal,
Live user journal and Wayland session logs into QEMU serial under the marker
`VEYRA LIVE GRAPHICS DIAGNOSTICS`. This service is restricted to QEMU and
is never part of the installed A/B system.

## October 27 KDE startup investigation

Run 40509124321 proved that `sddm-helper` authenticated `liveuser`, but
the Wayland session exited with code 127. The live RPM installroot had no
`pam_systemd.so`; the Plasma D-Bus wrapper relies on `dbus-run-session`
when no session bus already exists. The builder now installs Fedora
`systemd-pam` and `dbus-daemon` and checks the four core Plasma/D-Bus
executables and PAM module before SquashFS creation.

Run 40518530598 passed kernel/init boot gates on BIOS and UEFI; logs
no longer report a Plasma exit-127 or missing PAM module, and KWin's
Wayland user service starts. **At 120 seconds both QEMU 2D screenshots
were still uniformly black**. This is NOT proof of a working Plasma
desktop. There is a known KWin 6.7 render-device regression affecting
virtio GPU without VirGL. The Live ISO therefore uses a systemd
user-environment generator to set `KWIN_RENDER_NODES=` only when the
guest is detected as QEMU/KVM **and** has the virtio_gpu DRM driver.
The installed Veyra image is unchanged. This is a compatibility
workaround, not an assertion that all VirtIO configurations work.

The QEMU validation now captures a later 180-second screenshot,
fails on entirely black final framebuffer images, and flags SDDM
session crashes and missing PAM. Green source tests or boot metadata
are not sufficient to declare graphical readiness. Until a nonblack
KDE screenshot and the installer flow have been validated,
treat the published ISO as an experimental live/diagnostic image and
do not install to a physical disk.

## Disposable Live guest: credentials, sleep and application installation

This **experimental** Live image uses SDDM autologin to a password-locked
`liveuser` account. Its KDE screen locker is disabled (`Autolock=false`,
`LockOnResume=false`, `RequirePassword=false`); Live suspend/hibernate
targets are masked and logind is configured to ignore idle suspend. This
prevents a no-password Live user from being stranded at an unlock prompt.
These policies are **not installed** into the real A/B Veyra system.

For console-only troubleshooting **inside the Live ISO**, the root account's
password is `veyra`. This is an intentionally public and weak development
credential, **not safe for production, shared machines or remote exposure**.
The image explicitly disallows SSH root login and SSH password authentication.
Do not copy this root-password setup into `mkosi.extra` or the installed
system, and remove it before a production release.

Live users can run `vpkg install PACKAGE_NAME` directly: a Live-only
`/usr/local/bin/vpkg` shim asks `sudo -n` to run **only**
`/usr/bin/vpkg install` with names matching a strict anchored
sudoers expression. The existing vpkg package allowlist, signature checks
and blocked-system-package rules are unchanged. Other `vpkg` subcommands,
arbitrary root commands and shell execution do not gain passwordless sudo.
There is no persistent A/B rollback or system OTA inside Live, and packages
installed in the writable overlay vanish on restart.

## Calamares launcher and installation safety (October 27)

The earlier ISO only wrote a `veyra-installer.desktop` entry to the
application menu, with `Exec=sudo -n /usr/bin/calamares`. Running a Qt
installer as root loses the authenticated KDE Wayland display/session
variables and can result in no visible window. The Live rootfs also installed
`konsole` but omitted the separate Fedora `konsole-part` package required
by Calamares's `interactiveterminal` view module.

The current experimental builder installs `konsole-part` and checks its
KParts plugin. It puts an executable `Install Veyra Next.desktop` shortcut
in `/home/liveuser/Desktop` and an entry in the application menu. The
shortcut launches `/usr/bin/calamares` **as liveuser**, preserving the
Wayland session; errors are written to
`~/.cache/veyra-live/calamares.log` and displayed through KDialog.

The Calamares interactive terminal runs a separate restricted installer
backend: `sudo -n /usr/libexec/veyra-installer --preflight` first verifies
that Recovery UKI and the other required inputs are present. Only on
successful preflight does it run `sudo -n /usr/libexec/veyra-installer` to
prompt for disk selection and explicit destructive confirmation. The Live
sudoers file allows these **two fixed argument forms only**, not an
arbitrary command, root GUI, or `--target` flags. Calamares's generic
`finished` view is deliberately removed because it cannot know whether
the separately executed terminal installer succeeded or failed.

**The Live ISO may be used to inspect the Calamares interface even when
installation is unavailable.** The published experimental pipeline does
not yet include a verified Recovery UKI or a successful real installed-OS
A/B boot test, so it must not be advertised as install-ready. The installer
must fail closed before changing a disk when its Recovery UKI is absent.
