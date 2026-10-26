# Veyra package repository

This tree contains source for Veyra add-on RPMs and the tools that build the
two package channels. `main` and `testing` are repository directories, not Git
branches. Fedora's system RPMs remain in Fedora's repositories and in the
Veyra A/B system image; these channels are reserved for packages maintained by
Veyra.

## Transaction boundary

`vpkg` has two transaction paths. Veyra-maintained RPMs are signed wrappers
around one `.raw` system-extension image and are installed in a separate RPM
root on writable `/var`, using libdnf5's Python API. For Fedora and RPM Fusion
applications, `vpkg` copies the base RPM database into a disposable libdnf5
installroot, resolves
and installs the requested RPM set there with RPM scriptlets and triggers
disabled. It classifies the resolved transaction from the installroot's actual
RPM records and payload: only additive `/usr` and `/opt` packages with no
package-defined scriptlets or triggers are accepted as `LIVE`. A transaction
that changes a base package is rejected; permitted staged system-package
generations are not implemented. The resulting `/usr` and `/opt` payload becomes a
persistent directory system extension named `vpkg-apps`; the running base RPM
database remains unchanged. Veyra wrapper RPM transactions create symlinks under
`/etc/extensions`; the app layer uses one generation pointer. Both refresh
`systemd-sysext` and restore the previous active generation if activation
fails.

The image also configures Fedora's official `fedora` and `updates` repositories
and RPM Fusion's free and nonfree repositories for package discovery and
dependency resolution. RPM Fusion is an independent third-party source; its
nonfree repository makes proprietary packages available. `vpkg install NAME`
accepts exact package names from those repositories, and `vpkg install
/path/to/package.rpm` accepts a local RPM signed by a key trusted by Fedora or
RPM Fusion. The Veyra `main`/`testing` channel selection applies only to
`veyra-*` packages. Fedora's base package set is still composed into the A/B
image.
The two RPM Fusion public keys are embedded in the image and their full
fingerprints are checked into this source:

* Free: `E9A491A3DE247814E7E067EAE06F8ECDD651FF2E`
* Nonfree: `79BDB88F9BBF73910FD4095B6A2AF96194843C65`

Base OS, kernel, bootloader, and other packages that change the system image
are not installable through `vpkg`; those belong in a signed A/B system update.
The app layer currently accepts only additive `/usr` and `/opt` payloads. It
rejects base-file collisions, packages that change a base RPM record, protected
kernel, graphics-driver, init, bootloader, RPM-database and firmware paths,
setuid/setgid files, Linux file capabilities, special files, and all payloads
outside `/usr` and `/opt`. Existing shared directories such as `/usr/bin` are
allowed, but existing files cannot be replaced. RPMs that ship `/etc`
configuration and local packages signed by third-party keys are not supported
until Veyra has a configuration-extension design and explicit key enrollment.
Packages that require scriptlets or triggers are rejected until vpkg has a
supported, confined way to process their effects. This is a limited app-layer
prototype, not full RPM layering; it does not yet provide staged system-package
generations or a general RPM scriptlet policy.
The repository RPM is a small wrapper around a version-compatible system
extension image. Extension images are built for Fedora 44 in this prototype
and must be rebuilt and tested when Veyra changes Fedora base versions. They
also carry the Veyra `SYSEXT_LEVEL` stored in
`mkosi.extra/usr/share/veyra/sysext-level`; bump that level only when the
extension ABI changes. Boot assessment rejects a new OS slot if a persistent
extension is not active under the new image.

The package-policy unit suite and functional package checks run from the
disposable Veyra guest in the QEMU/KVM image workflow. The package build
workflow only validates RPM/repository artifacts; it does not execute `vpkg`
against the CI runner's RPM database.

## Channels and publication

Veyra RPM clients consume versioned GitHub Release snapshots from
`angyedz/veyra-pkgrepo`; they do not fetch RPMs from raw GitHub or GitHub Pages.
Stable uses the highest valid non-prerelease `rpm-vX.Y.Z` tag. Testing uses the
highest valid prerelease `rpm-vX.Y.Z-testing` tag. Each release must publish
`veyra-rpm-repo-x86_64.tar.gz` and the detached
`veyra-rpm-repo-x86_64.tar.gz.sha256` asset. `vpkg` downloads those assets via
HTTPS, checks the archive SHA256, safely extracts the repository, then verifies
`repodata/repomd.xml.asc` with the image-pinned Veyra public key before exposing
the local snapshot to libdnf5. Fedora and RPM Fusion remain their separately
configured remote repositories. Veyra channels do not mirror Fedora or RPM
Fusion.

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

The manual **Publish Veyra RPM repository** workflow may continue to publish
development channel mirrors. Production clients use the signed versioned
release snapshots above. The release workflow must attach the repository
archive and its detached checksum alongside signed RPMs and signed metadata;
tag policy and release assets are defined in `veyra-pkgrepo`.
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
