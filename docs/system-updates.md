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
assessment checks the required boot targets. A trial UKI is marked good only
when those targets succeed; repeated failed boots are expected to fall back to
the previously working UKI. That final A→B and rollback behavior still needs
to be demonstrated in a VM before this design is considered validated.

## Update signing and publication

`Verify=yes` makes `systemd-sysupdate` verify the detached signature on
`SHA256SUMS.gpg`, then verify each downloaded file against that manifest. The
public key must be available in the image as
`/usr/lib/systemd/import-pubring.gpg`. The manual workflow builds an image with
that public key, signs the root/verity/UKI checksums, and publishes those update
assets as a GitHub release. It does not publish the full disk image or an
installer.

Before using the workflow, provision a dedicated Veyra system-update signing
key in the `Veyra-next` repository:

* Store the armored private key in the `VEYRA_UPDATE_SIGNING_KEY` Actions
  secret.
* If the key is protected by a passphrase, store that passphrase separately as
  `VEYRA_UPDATE_SIGNING_PASSPHRASE`.
* Keep an offline backup and revocation material. Do not reuse the RPM package
  signing key for system updates.

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
