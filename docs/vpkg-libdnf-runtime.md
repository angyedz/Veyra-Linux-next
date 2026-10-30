# vpkg libdnf5 runtime contract

`vpkg` uses the Fedora-provided Python bindings and calls `libdnf5.base.Base`,
`Goal`, and `Transaction` directly. The runtime image and the packaged `vpkg`
RPM must require `libdnf5` and `python3-libdnf5`. Neither must require or install
the `dnf5` command-line package or `dnf5-plugins`.

Build and repository-fixture containers may use the DNF CLI to prepare RPMs,
repositories, and test images. Runtime `vpkg` code must not spawn that CLI.

For this image line, `vpkg` validates the selected deployment's
`/usr/lib/os-release` and accepts only `ID=fedora`, `VERSION_ID=44`. It passes
that validated version to libdnf5 before loading repositories. Image and
package gates must verify the configured Fedora target and reject RPMs from a
newer release than that target. The check follows the configured target when a
future Fedora release is adopted; it does not permanently exclude any release.

The API adapter disables plugins, weak dependencies, RPM scriptlets and
triggers by default; enables RPM package signature checking; explicitly selects
the configured repository IDs; and treats dependency resolution or signature
errors as a rejected transaction. Candidate app transactions use their
disposable RPM database copied from the selected base. The official base RPM
database is not an install target.

The binding and library are tied by Fedora package dependencies. The vpkg RPM
should declare both `Requires: libdnf5` and `Requires: python3-libdnf5` for a
clear runtime contract; it must not declare `Requires: dnf5`.

## Fedora 44 SWIG repository callback compatibility (2026-10-28)

The first real signed `vpkg install veyra-shell-tools` in disposable QEMU
reached the Python libdnf5 adapter and failed at
`RepoWeakPtr_set_callbacks`: Fedora 44's generated SWIG binding expects an
owning `std::unique_ptr<libdnf5::repo::RepoCallbacks>`, not the Python
subclass that was being passed to `repo.set_callbacks()`. Keeping a Python
reference to the subclass does not fix this signature mismatch.

The adapter no longer invokes that incompatible callback. For both Veyra
channels it now independently overwrites any repository-configured
`gpgkey` with the **single pinned**
`file:///usr/share/veyra/RPM-GPG-KEY-VEYRA`, and explicitly forces
`repo_gpgcheck=True` and `pkg_gpgcheck=True`. Repository metadata and
RPM signature checks remain compulsory. The guest also imports the identical
public key into its private RPM installroot. A callback error must never be
"fixed" by switching off signature verification or enabling an arbitrary
network key source.

Unit tests in `tests/test_vpkg_libdnf.py` assert enforced trust settings
and reject reintroduction of an incompatible callback. **The actual Fedora
44 Python-libdnf5 transaction must still pass in a new real QEMU guest**;
source tests alone do not prove the signed RPM install, upgrade, removal, or
rollback.
