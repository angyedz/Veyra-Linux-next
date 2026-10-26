# vpkg native generations: design and implementation boundary

**Status:** the current implementation is a safe additive application-layer
prototype with rollback metadata. It is not yet a complete native RPM manager.

## Requirement audit

| Master specification | Current implementation | Remaining gap |
|---|---|---|
| Classify the resolved transaction as LIVE, STAGED, or REJECT | libdnf5 resolves the complete request in a disposable installroot; additive app transactions are LIVE; a non-core base-RPM delta is classified STAGED; explicit app-layer staging is supported | The current executor rejects STAGED system-RPM deltas because there is no native system-root generation backend yet |
| Analyze dependencies, paths, conflicts, hooks, ABI, and privileges | Checks full base RPM inventory deltas, payload paths, collisions, file types, setuid/setgid, capabilities, protected package names, scriptlets, triggers, file triggers, and SYSUSERS using the machine-readable policy | `/etc` is rejected; unit handling needs real package coverage; ABI analysis is limited to sysext compatibility |
| Keep arbitrary RPM scriptlets from running with host privileges | libdnf5 uses `noscripts,notriggers`; any package declaring hooks or SYSUSERS metadata is rejected | No constrained effect adapter or scriptlet executor; unsupported hooks remain REJECT |
| Install native RPM files on immutable EROFS | Additive `/usr` and `/opt` content is exposed through systemd-sysext | No full root overlay, `/etc` layer, generation-owned host RPM database, or system-package replacement |
| Preserve and restore package generations | App payload directories and transaction manifests live under `/var/lib/veyra/vpkg/app-layers`; rollback changes the active generation and refreshes systemd-sysext. `vpkg stage install` records a trial marker and selects its generation after reboot, before sysext activation; a failed candidate can be rolled back from a text console | ext4 directories are copied, not snapshots; storage use grows with retained generations; no separate Recovery boot entry |
| Coordinate with OTA identity and requests | Explicit requests/pins and bindings use `(roothash, IMAGE_VERSION)`; prepare/bind/complete/cancel APIs exist, and OTA pending blocks vpkg mutations | The shared VM integration run must still prove cross-process locking and trial-UKI behavior |

The QEMU fixture is designed to exercise signed package install, upgrade,
remove, scriptlet rejection, systemd unit discovery, app rollback, immutable
base RPM database, candidate conflict preservation, pinned request
re-resolution, pending locks, and pair-keyed deployment bindings. These
scenarios have not yet been run in a valid guest matching the configured
Fedora release. A guest reporting a different release is not valid evidence.
The runtime image uses Python bindings and the library, not the DNF5 CLI.
Build-only containers may still use that CLI.

vpkg reads `ID` and `VERSION_ID` from the selected base root's
`/usr/lib/os-release`, resolving symlinks within that root without sourcing or
executing the file. Current package transactions and OTA candidate preparation
require `ID=fedora` and `VERSION_ID=44`. The validated selected-base version is
passed to libdnf5 and used for sysext `VERSION_ID`. Fedora 44 is the current
target policy, not a permanent restriction on future Fedora targets.

The immutable policy at `/usr/share/veyra/vpkg/protected-core.json` uses schema
1. It declares the current Fedora 44 base identity, protected RPM names and
prefixes, the only permitted additive payload roots, and exact/prefix protected
paths. Missing, malformed, unknown-schema, or wrong-target policy data fails
closed before a package request or payload is accepted. The policy is shipped
by the `veyra-vpkg` RPM as well as the image build source. Its protected RPM
names cover the image's init, boot, kernel/firmware, package-manager and
integrity stack, including `libdnf5` and its Python bindings; `/usr/lib/veyra`
and `/usr/share/veyra` are protected payload roots for Veyra-owned userland.

## Storage contract under the current image layout

The disk remains unchanged: `/var` is a fixed-size 12 GiB ext4 partition,
`/home` is a separate 24 GiB ext4 partition, and the documented VM minimum stays
64 GiB. vpkg stores generations in
`/var/lib/veyra/vpkg/app-layers/<generation-id>` and keeps compact intents,
bindings, pending markers, and transaction records under
`/var/lib/veyra/vpkg/`. The package payload and metadata share the existing
`/var` capacity with logs, caches, and other state. No capacity is reserved and
no Btrfs snapshot semantics are assumed.

Each prepared generation is a new directory with a complete app payload tree,
local RPM sources, and a manifest. This makes a generation independent of the
previous package tree and allows a pointer-based rollback. Since ext4 has no
subvolume snapshots, retaining generations consumes space proportional to the
package trees retained. The manager must check free space before building,
fail before activation when space is insufficient, never remove a generation
referenced by a deployment binding or pending OTA, and expose any future
cleanup as an explicit operation. This is a functional limit of the current
layout; there is no fixed generation pool or promise of cheap snapshots.

## Transaction behavior

The classifier consumes the solved RPM result, not a product-name shortcut.

- **LIVE:** additive compatible package payload under `/usr` or `/opt`, with no
  unsupported scriptlet, trigger, SYSUSERS action, protected-path change, base
  RPM replacement, or file collision. Build and verify an inactive generation,
  refresh systemd-sysext, reload the system unit catalogue when the generation
  contains system units, then atomically publish intents and the deployment
  binding. A new service is never started or enabled automatically.
- **STAGED:** `vpkg stage install` can prepare an additive app-layer generation
  and persist the exact base pair, previous generation, requests, and origin
  boot ID. The boot sync unit selects it only on a later boot and only for the
  matching pair; the health check completes the trial after sysext activation.
  `vpkg rollback` can restore the previous app generation from a text console.
  This does not stage arbitrary host RPM replacements or `/etc` changes. An
  replacement or removal of an unprotected RPM already present in the base is
  identified as a STAGED candidate by transaction analysis, but the executor rejects
  it because the required root-overlay generation mechanism is not installed.
- **REJECT:** protected boot/kernel/driver/systemd-core/RPM files, `/etc`
  changes without a
  generation-owned configuration layer, unresolved conflicts, unsafe file
  types or privileges, ABI incompatibility, and unsupported RPM hooks. A
  rejection leaves the active generation, intents, and old binding unchanged.

RPM scripts stay disabled in the installroot. If a package needs system-user,
cache, or service actions, reject it unless a versioned adapter declares the
specific effect, confines it to the candidate state, and verifies the resulting
diff. A generic root-capable sandbox is not sufficient evidence that an
arbitrary scriptlet is safe.

## Generation protocol

Every generated app layer records its ID, parent generation, transaction kind,
creation time, explicit requests and pins, resolved NEVRAs, base roothash, and a
deterministic SHA-256 digest of the payload tree. The payload is flushed before
the manifest is atomically published. Activation verifies the digest before
switching the pointer. The previous generation is retained. `vpkg rollback`
selects only a parent generation for the currently running base identity,
verifies it, restores its explicit requests, refreshes systemd-sysext, and
updates the exact deployment binding. It does not roll back `/home` or
unrelated `/var` state.

The digest detects accidental or partial corruption; it is not an authenticity
boundary against root, who can change both the data and its manifest. The
current system RPM database stays untouched. Generation manifests record
resolved app NEVRAs, but `rpm -q` on the host still does not see app packages as
installed RPMs. Persisting and exposing the generation RPM database is part of
the future native-layer work.

## OTA contract

Keep the current protocol fields stable:

- `/var/lib/veyra/vpkg/intents.json` stores explicit requests and exact pins.
- `prepare-for-base` resolves those requests against the candidate root and
  returns a structured status and inactive generation path.
- `bind-generation` checks that prepared requests still equal the saved
  requests, binds the generation to `(roothash, IMAGE_VERSION)`, and creates
  the pending lock before OTA exposes the trial UKI.
- Unsatisfiable requests or candidate file conflicts leave the old pointer,
  binding, and intents unchanged.
- `vpkg sync` selects by both identity fields before systemd-sysext. A mismatch
  does not release the lock.
- Exact-target health and boot blessing precede `complete-pending`; OTA removes
  the exact trial entry before `cancel-pending`.

OTA consumes the CLI/JSON protocol and must not depend on generation contents
or internal filesystem paths beyond the returned path. Existing explicit
generation directories remain in ext4 `/var` so an A/B fallback can select its
binding.

## Path to the remaining native RPM requirements

The existing filesystems can host OverlayFS upper and work directories under
`/var/lib/veyra/vpkg`, but a complete implementation still needs the boot
integration that selects a generation before `switch-root`. The next safe
vertical slice is:

1. Store a complete candidate upper and cloned RPM database in each generation
   directory on ext4; verify capacity and all package-file effects before
   publication.
2. Add an early-boot selector keyed by the current verity roothash and UKI
   `IMAGE_VERSION`. It mounts the selected upper over the EROFS root and a
   separate generation-owned `/etc` upper over the persistent `/etc` lower.
3. Bind the selected generation RPM database at the host RPM database path,
   preserving the base database inside immutable EROFS.
4. For LIVE additive transactions, keep the existing sysext refresh path and
   write an equivalent boot upper for the next boot. For `/etc`, system RPM
   replacements, or restart-sensitive units, use STAGED trial UKIs with boot
   counting and a rollback entry for the previous `(roothash, IMAGE_VERSION)`
   pair.
5. Add Recovery boot that ignores the package upper and runs a small immutable
   recovery helper, so a corrupt generation cannot prevent rollback.

Ext4 directory generations are a correctness-first prototype, not a
space-efficient snapshot system. The manager must retain referenced history,
report storage requirements before staging, and reject a transaction when the
existing `/var` has insufficient free space. No repartitioning or data-format
migration is part of this design.
