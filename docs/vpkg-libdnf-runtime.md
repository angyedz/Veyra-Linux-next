# vpkg libdnf5 runtime contract

`vpkg` uses the Fedora-provided Python bindings and calls `libdnf5.base.Base`,
`Goal`, and `Transaction` directly. The runtime image and the packaged `vpkg`
RPM must require `libdnf5` and `python3-libdnf5`. Neither must require or install
the `dnf5` command-line package or `dnf5-plugins`.

Build and repository-fixture containers may use the DNF CLI to prepare RPMs,
repositories, and test images. Runtime `vpkg` code must not spawn that CLI.

For this image line, `vpkg` validates the selected deployment's
`/usr/lib/os-release` and accepts only `ID=fedora` and the release configured
for the image. It passes that validated version to libdnf5 before loading
repositories. Image and package gates verify the same configured release and
reject artifacts from a different Fedora release or Rawhide. Changing the base
release updates this target; no future Fedora release is permanently excluded.

The API adapter disables plugins, weak dependencies, RPM scriptlets and
triggers by default; enables RPM package signature checking; explicitly selects
the configured repository IDs; and treats dependency resolution or signature
errors as a rejected transaction. Candidate app transactions use their
disposable RPM database copied from the selected base. The official base RPM
database is not an install target.

The binding and library are tied by Fedora package dependencies. The vpkg RPM
should declare both `Requires: libdnf5` and `Requires: python3-libdnf5` for a
clear runtime contract; it must not declare `Requires: dnf5`.
