# Veyra package repository

This tree contains source for Veyra add-on RPMs and the tools that build the
two package channels. `main` and `testing` are repository directories, not Git
branches. Fedora's system RPMs remain in Fedora's repositories and in the
Veyra A/B system image; these channels are reserved for packages maintained by
Veyra.

## Transaction boundary

The initial `vpkg` prototype uses DNF5 to resolve Veyra package dependencies
inside a separate installroot on the writable `/var` partition. This keeps both
the RPM database and DNF5 transaction state away from Fedora's immutable
`/usr/lib/sysimage` database. It accepts only signed packages named `veyra-*`,
with no RPM scripts and with a payload consisting solely of a `.raw`
system-extension image under the separate installroot's
`/var/lib/extensions/`. It validates the complete downloaded transaction before
applying it, limits DNF5 to the selected Veyra channel, creates symlinks under
`/etc/extensions`, refreshes `systemd-sysext`, and asks DNF5 history to undo the
RPM transaction if the extension cannot be activated. This lets additive
`/usr` and `/opt` content be activated without changing the base RPM database or
rebooting.

Base OS, kernel, bootloader, and other packages that change the system image
are not installable through `vpkg`; those belong in a signed A/B system update.
The repository RPM is a small wrapper around a version-compatible system
extension image. Extension images are built for Fedora 44 in this prototype
and must be rebuilt and tested when Veyra changes Fedora base versions.

## Channels and publication

The configured repository locations are:

* `https://angyedz.github.io/Veyra-next/packages/main/$basearch/`
* `https://angyedz.github.io/Veyra-next/packages/testing/$basearch/`

Both repos require RPM signatures and signed `repomd.xml` metadata. The
publisher must sign each RPM and repository metadata with the same trusted
Veyra RPM key. No release key is checked into this repository, and publication
fails closed until the owner provisions `VEYRA_RPM_SIGNING_KEY` in GitHub
Actions. Keep an offline backup and revocation material for this dedicated key;
do not reuse the separate system-update signing key. CI uses a throwaway key
only to verify the build and repository-signature path; it is never a client
trust key.

The installed image embeds the public half of this key at
`/usr/share/veyra/RPM-GPG-KEY-VEYRA`; its repository files point to that local
copy. The signed update image is therefore the trust bootstrap for Veyra RPMs.
Clients do not fetch a new trust key from the same website that serves the RPMs.
The system release workflow requires `VEYRA_RPM_SIGNING_KEY` and exports only
its public key into the image. Repository key rotation must be delivered in a
signed system update before publishing packages signed only by the replacement
key.

An ordinary source checkout has no production public key because no key is
committed to Git. Locally built images therefore keep both Veyra repositories
disabled and cannot use `vpkg` until a trusted key is supplied. The CI image
uses a short-lived test key; the signed system-release workflow embeds the
public half corresponding to `VEYRA_RPM_SIGNING_KEY`.

The manual **Publish Veyra RPM repository** workflow publishes a rebuilt
`testing` snapshot or promotes the exact already-signed testing RPMs into
`main`. Promotion requires the package name to appear in both channel manifests.
Before each deployment it downloads the current channel RPMs, validates signed
metadata, checks RPM checksums and signatures, and preserves all existing
versions. A package filename may not be replaced by different bytes; bump its
RPM version/release for changed content. The workflow publishes both channel
directories and their signed metadata together as a GitHub Pages artifact.
The publisher pins the existing site's RPM key to the signing secret's
fingerprint and refuses an unplanned key change.
Before the first deployment, set the repository's Pages source to **GitHub
Actions** in **Settings → Pages**; the repository does not have a Pages site
enabled yet.

The RPM workflow expects an unencrypted, dedicated signing key in
`VEYRA_RPM_SIGNING_KEY` because `rpmsign` runs non-interactively. Keep an
offline protected backup of the key and revocation material. To add a prepared
armored signing-key export to GitHub Actions:

```sh
gh secret set VEYRA_RPM_SIGNING_KEY < veyra-rpm-signing-key.asc
```

Never store that private-key file in Git. A separate
`VEYRA_UPDATE_SIGNING_KEY` is used for OS updates.

The first package source is `veyra-shell-tools`, an optional set of
`bash-completion`, `fzf`, `jq`, and `ripgrep` tools. Its binary payload is built
as a Fedora 44-compatible system extension, then wrapped in an RPM. The
repository builder rejects RPMs that do not follow the Veyra extension payload
policy.
