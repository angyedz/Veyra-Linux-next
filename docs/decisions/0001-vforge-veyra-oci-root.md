# ADR-0001: Build Vforge environments from a Veyra OCI base

## Status
Proposed. The current Fedora-based prototype does not implement this design; the OCI build and Fedora 44 QEMU validation are still required.

## Date
2026-10-26

## Context
Vforge currently asks Podman to create `registry.fedoraproject.org/fedora:44`, then overlays Veyra `/usr` and the host RPM database. `/etc`, `/var` and the rest of the container root remain Fedora. This does not give an environment the Veyra system base and it gives package transactions an unsafe boundary around host RPM state.

The environment must run Vpkg against its own root and RPM database, keep writes private, retain persistent container changes, and never expose mutable host `/etc`, `/var` or `/home` as its base. Container storage must use ordinary free space at the user-selected location; this design adds no disk partition.

## Decision
Build a Veyra OCI root image from the same Fedora 44 Veyra package/image definition used for the installed system. The artifact must carry a Veyra build identity (including the system image version and root hash) and a package inventory that can be checked against the matching system release. Build it from the image build tree before mutable host state is introduced; do not derive it by copying the running host's `/etc`, `/var` or `/home`.

Vforge will ask rootless Podman to create environments from that Veyra OCI image. Podman's private writable container layer provides persistent copy-on-write state in its graphroot. The shared graphroot is stored under the user's configured common container location; project-specific container state is stored under the project's `.vforge/` directory. The immutable base image is stored in the same selected storage context as its environments.

Vpkg transactions inside an environment use libdnf5 against that environment's `/` and its private RPM database, cache and history. Vpkg must fail closed unless the environment has a valid Veyra base identity and a private root marker. It must not invoke the DNF CLI, host `systemd-sysext`, or host generation staging.

Vforge supplies a root-owned read-only bind marker at
`/run/veyra-vforge/root.json`. Its schema is `1`, kind is `vforge-veyra-root`,
and it records a per-environment `root_id`, `image_id=veyra-next`, matching
`image_version`, the SHA-256 digest of the selected OCI manifest,
`storage_driver=podman-overlay`, and `immutable_base=true`. Vpkg checks this
identity against the rootfs `/usr/lib/os-release`, requires Fedora 44 and a
private `/usr/lib/sysimage/rpm`, and rejects separate mounts over `/usr` or its
managed package state and cache. Rootless Podman may report
`mount_program=fuse-overlayfs` when its overlay driver needs it. The marker
contract is implemented in `docs/vpkg-container-mode.md` on the Vpkg branch;
Vforge still needs to create and validate it when provisioning a container.

An environment records the base identity it was created from. A Veyra OTA does not rewrite a running environment's base. Vforge must either keep using its recorded base or explicitly create a candidate from the new Veyra OCI image and switch only after Vpkg resolution and health checks succeed. The exact migration and retention policy needs runtime validation before it is considered implemented.

## Alternatives considered

### Fedora root with Veyra `/usr` and RPM database overlays
Rejected as the target architecture. It leaves the root mostly Fedora, mixes the package database with host state and cannot satisfy the Veyra-root requirement. It remains useful only as an explicitly named prototype while the new OCI path is built.

### Mount the host root directly into each container
Rejected. The host's `/etc` and `/var` are mutable and may contain machine-specific state; mounting the full root also creates recursive mount and write-isolation hazards. A release-built immutable OCI root gives each environment a reproducible base without exposing host state.

### Separate Btrfs partition or preallocated container volume
Rejected. The user requires containers to use ordinary free space in a location they choose, without imposing a dedicated partition or reserving a fixed amount of disk.

## Consequences
- Vforge must stop treating the Fedora 44 upstream image as its integrated/trusted base.
- The image build and GitHub Release workflow must produce and publish a Veyra OCI artifact alongside the matching system update.
- Podman becomes responsible for the normal per-container writable layer; Vforge must verify that snapshots and rollback preserve this layer and its identity safely.
- Each environment's Vpkg RPM database and state are private to that OCI container root.
- OCI artifact generation, release download/verification, storage behavior, Vpkg transactions, OTA compatibility and rollback still require Fedora 44 Veyra QEMU tests before this proposal can be accepted.
