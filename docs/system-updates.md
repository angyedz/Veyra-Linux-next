# Veyra system updates

## Firmware support status

The tested OTA path currently targets UEFI with systemd-boot and trial UKIs.
The code also has a Legacy BIOS path: `veyra-update` stages a kernel and
initramfs and GRUB tracks three trial boots, blessing, and fallback. The QEMU
gate currently checks only that the BIOS image reaches `graphical.target`;
BIOS OTA activation, successful blessing, and rollback have not yet passed an
end-to-end SeaBIOS test. Do not advertise BIOS updates as supported until that
test passes.

## Transaction flow

The image contains two root partitions, each paired with dm-verity data. A
system release contains the compressed root image and its matching verity
image, plus a versioned UKI for UEFI and a kernel/initramfs pair for Legacy
BIOS. `systemd-sysupdate` writes the root and verity payloads to the unused
slots first. After `vpkg` preflight, it installs the firmware-specific boot
artifacts last: a three-attempt UKI for UEFI or versioned kernel/initramfs for
GRUB. The current OS version is protected while staging the update.

The installed command is `veyra-update`:

```sh
veyra-update list
veyra-update check
sudo veyra-update install
veyra-update pending
sudo veyra-update reboot
sudo veyra-update cancel
```

Staging and rebooting are separate actions. On systemd 259, `veyra-update`
first stages the signed root and matching dm-verity partitions in the inactive
slot, without publishing boot artifacts. It verifies their root hash and mounts the
candidate EROFS root read-only so `vpkg` can resolve the saved explicit package
intents and version pins against that base. A solver conflict leaves the
currently bootable deployment and package generation selected; the inactive
root and verity partitions may remain staged, but no trial boot entry is installed.

After a successful preflight, `vpkg` writes the resolved generation binding
for the candidate `(root hash, IMAGE_VERSION)` identity and records a pending
OTA transaction that blocks further package mutations. Compatibility is
resolved against the root hash; the image version distinguishes deployments
whose EROFS bytes happen to share a root hash. Only then does `veyra-update`
install the firmware-specific boot artifacts. If their installation or trial
activation fails, the updater removes those unblessed artifacts and cancels
the pending binding. Use
`sudo veyra-update cancel` to explicitly cancel a staged trial. The pending
package lock is released after the firmware-specific boot check blesses the
matching root hash and image version, or after the fallback path verifies that
the candidate exhausted its attempts. Merely booting the old deployment while
the trial remains eligible does not release the lock.

After reboot, `vpkg` selects the package generation bound to both the running
UKI's dm-verity root hash and `IMAGE_VERSION` before systemd-sysext activation.
The `pending` and `reboot` commands share the updater lock and report/act only
when a trial UKI matches the pending identity; root and verity partitions left
by a preflight conflict do not count as a pending update. If the trial fails and
systemd-boot returns to the prior root hash, its earlier generation remains
selected. systemd's boot assessment checks the required boot targets, including
validation that all installed Veyra extensions activated. A trial UKI is marked
good only when those targets succeed; repeated failed boots fall back to the
previously working UKI. Selecting the old UKI manually while a trial is still
available keeps package mutations locked; the old boot remains healthy. The
lock is released only after the matching target is blessed, the trial is
explicitly canceled, or the exhausted target UKI is verified and removed.

System journals use the persistent `/var/log/journal` directory, so logs from
a failed trial boot remain available after systemd-boot returns to the previous
UKI. Journald is capped at 1 GiB and keeps at least 1 GiB free on the `/var`
partition. After rollback, inspect recent boots with `journalctl --list-boots`
and `journalctl -b -1`. This depends on the persistent `/var` partition being
readable; standalone Recovery access is still unimplemented.

## Kernel scope

The current image ships the Fedora kernel. `vpkg` does not install kernels,
and custom kernel installation is outside the current product scope. The
kernel shipped by Veyra is part of each A/B system image, so an update replaces
it with the kernel in the new image. A separately copied UKI on the EFI
partition might remain as a file, but Veyra does not manage or validate it, and
its kernel modules may no longer match the updated system.

CI exercises that path with a disposable disk image. It signs a temporary
manifest, creates a fresh dm-verity tree for the built EROFS root, applies the
update with `systemd-sysupdate`, and boots the new slot in QEMU/OVMF. The test
checks the new root hash, `graphical.target`, `boot-complete.target`, successful
boot blessing, and GPT read-only flags on the updated root and verity
partitions. It then adds a test-only UKI that enters `emergency.target`, boots
it three times, verifies each try counter, and checks that the next boot
returns to the blessed slot. Each failed boot writes and syncs a unique marker
to the persistent journal; the fallback boot must read that marker from
`journalctl -b -1` before it reaches the boot-complete target. The failure UKI
and test-only systemd units are injected directly into this temporary image;
this does not publish a release or assert that an application rollback is
already available to users.

System-extension images carry the Veyra `SYSEXT_LEVEL` in addition to the
Fedora release. Before blessing a new slot, the boot health target waits for
`systemd-sysext`, checks that every installed Veyra wrapper and the persistent
`vpkg-apps` layer are active, and rejects app-layer paths that collide with
files in the new base RPM inventory. A slot with an incompatible extension or
new base-file collision is not accepted as a good boot.

The image workflow also writes disposable markers to the separate `/etc`,
`/var` and `/home` partitions before staging the update, then checks those
markers after the new slot boots. On that same boot it points `vpkg` at a
temporary signed test repository and performs install, upgrade and remove
transactions. The fixture verifies that `systemd-sysext` exposes the installed
commands, removes them again, and leaves Fedora's base RPM database untouched.
It also attempts to install a signed but deliberately invalid extension image
and verifies that the real `systemd-sysext` failure rolls the DNF transaction
back while leaving the valid extension active. A separate DNF5 transaction
test uses a controlled activation failure to exercise the rollback path
reliably.

## Update signing and publication

`Verify=yes` makes `systemd-sysupdate` verify the detached signature on
`SHA256SUMS.gpg`, then verify each downloaded file against that manifest. The
public key must be available in the image as
`/usr/lib/systemd/import-pubring.gpg`. The manual workflow builds an image with
that public key, signs the root/verity/UKI checksums, and publishes those update
assets as a GitHub release. It does not publish the full disk image or an
installer.

Before using the workflow, provision two dedicated signing keys in the
`Veyra-next` repository:

* Store the armored system-update key in `VEYRA_UPDATE_SIGNING_KEY`.
* Store the armored RPM repository key in `VEYRA_RPM_SIGNING_KEY`. The release
  image embeds its public half at `/usr/share/veyra/RPM-GPG-KEY-VEYRA`, and the
  repository configuration uses that local file as its only key source. This
  prevents the package site from bootstrapping its own trust by serving a new
  key alongside packages.
* If the key is protected by a passphrase, store that passphrase separately as
  `VEYRA_UPDATE_SIGNING_PASSPHRASE` (the update-signing workflow currently
  supports it; the RPM signing key must be usable non-interactively by
  `rpmsign`).
* Keep offline backups and revocation material. Do not reuse one key for both
  system updates and RPM packages.

After the secrets are set and this code is merged into `main`, run **Veyra
signed system update** manually from the Actions tab. The workflow refuses to
publish if the key is missing, if it contains multiple private keys, or if
image/artifact validation fails. It creates a normal GitHub release so the
configured `releases/latest/download` update source resolves the signed
manifest. The release title says pre-alpha; it is not marked as a GitHub
prerelease because GitHub's latest-release download URL is used by the current
systemd-sysupdate source.

## Security boundary

The manifest signature authenticates the downloaded update bundle, and
dm-verity checks the root image while it is in use. The current UKIs are
unsigned. This therefore does not provide Secure Boot or protect the EFI boot
path from someone who can modify the boot partition. The prototype must not be
described as Secure-Boot secured.

## GitHub Releases-only QEMU update trial (not yet production-ready)

To try the real OTA experience, **the initial installed image and its update
must share the same pinned Veyra update key**. A random CI-generated key or a
public checksum downloaded beside an asset is not a trust bootstrap. The
installable Live ISO also needs the independently built Recovery UKI; installer
preflight MUST NOT be bypassed when that UKI is absent.

1. Install a release-signed Veyra Next base image `0.1.0` in QEMU with
   Secure Boot disabled, ideally using a sparse 64 GiB virtual disk; the
   current fixed repart recipe needs about 55.6 GiB, so a 30 GiB guest disk
   **cannot** be used for this installer. The installer asks for the user's
   name, user password and a separate root password. The public Live
   `root:veyra` credential is **never** copied to the installed image.
2. Set the dedicated `VEYRA_UPDATE_SIGNING_KEY` and
   `VEYRA_RPM_SIGNING_KEY` Actions secrets. The package repository uses
   the matching RPM private key under its own
   `VEYRA_RPM_GPG_PRIVATE_KEY_B64` secret. Import **the same public
   keys** into the base installer image before it is released. There is no
   automatic cross-repository secret sharing.
3. After review and merge of the release workflow, run the **Veyra signed
   system update** workflow on `main` with a *new* version (for example
   `0.1.1`). The workflow composes root/verity/UKI/kernel/initrd, signs
   the five-file checksum manifest, enforces a **2,000,000,000-byte cap per
   asset**, publishes a versioned GitHub Release and downloads the published
   assets back for SHA-256 and GPG verification. It refuses to overwrite a
   release tag. It does not publish an oversized raw disk image.
4. In the booted installed QEMU guest:

   ```sh
   veyra-update check
   sudo veyra-update install 0.1.1
   veyra-update pending
   sudo veyra-update reboot
   ```

   The new EROFS/dm-verity root is written into inactive A/B partitions.
   The matching versioned UEFI UKI or BIOS kernel/initrd is installed only
   after vpkg compatibility preflight. The trial can attempt three boots
   before returning to the previously blessed OS.

5. For optional Veyra RPMs, first publish signed `rpm-vX.Y.Z-testing`
   GitHub Release assets from `angyedz/veyra-pkgrepo`, and promote the same
   signed bytes to `rpm-vX.Y.Z` after testing. The installed image pins the
   RPM key. `vpkg` downloads only the two named Release assets, checks their
   SHA-256 and signed `repomd.xml`, and rejects metadata not signed by that
   key. Fedora and RPM Fusion remain independent upstreams.

**Current blocker:** this workflow and its installed-image trust bootstrap
are in Draft PRs. Neither repository currently has a published Release or
production signing key provisioned through this chat. Source/CI tests do not
prove QEMU install/firstboot/OTA or real GitHub Release downloads. Never
interpret a built Live ISO without Recovery UKI as safe to install.
