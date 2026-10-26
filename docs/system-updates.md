# Veyra system updates

## Transaction flow

The image contains two root partitions, each paired with dm-verity data. A
system release contains the compressed root image, its matching verity image,
and a versioned UKI. `systemd-sysupdate` writes the root and verity payloads to
the unused slots first and installs the UKI last. The UKI filename starts with
three boot attempts. The current OS version is protected while staging the
update.

The installed command is `veyra-update`:

```sh
veyra-update list
veyra-update check
sudo veyra-update install
veyra-update pending
sudo veyra-update reboot
```

Staging and rebooting are separate actions. After reboot, systemd's boot
assessment checks the required boot targets, including validation that all
installed Veyra extensions activated. A trial UKI is marked good only when
those targets succeed; repeated failed boots fall back to the previously
working UKI.

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
returns to the blessed slot. The failure UKI is injected directly into this
temporary test image; this does not publish a release or assert that an
application rollback is already available to users.

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
and verifies that the real `systemd-sysext` failure rolls the libdnf5-backed
transaction back while leaving the valid extension active. A separate
libdnf5-backed transaction test uses a controlled activation failure to
exercise rollback. A two-boot QEMU fixture also tests staged generation
selection and rollback before `graphical.target`.

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
