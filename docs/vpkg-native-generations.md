# Vpkg native system generations

**Status:** implementation prototype; not enabled as a supported package workflow.

## Goal and storage model

The base `/usr` is immutable EROFS and the existing `/etc` and `/var` partitions
are ext4. Native Vpkg system generations live under
`/var/lib/veyra/vpkg/system-generations/<roothash>--<IMAGE_VERSION>/` and use
the existing storage dynamically. No new partition, fixed-size pool, or disk
resize is part of this design. A transaction must check available space and
leave the active generation untouched if it cannot create a complete candidate.

Each generation directory contains:

```text
<generation-id>/
  manifest.json
  upper/usr/       # this generation's /usr delta and RPM database overlay
  upper/etc/       # this generation's /etc delta
  work/usr/        # OverlayFS workdir, same ext4 filesystem as upper/usr
  work/etc/        # OverlayFS workdir, same ext4 filesystem as upper/etc
```

The identity directory has an `active` symlink containing a generation ID. The
base generation copies `/usr/lib/sysimage/rpm` into its own `upper/usr`, leaving
the immutable image's RPM database unchanged. Later generations own a separate
RPM database copy and refer to their parent generation in the manifest. Parent
uppers are read-only lower layers before the EROFS base; the new generation's
upper is the writable top layer. This preserves OverlayFS whiteouts and avoids
copying all unchanged package payloads for every transaction. `/usr` is mounted
read-only for normal operation; `/etc` remains writable and its changes are
scoped to the selected generation.

Generation identity is exactly `(dm-verity roothash, UKI IMAGE_VERSION)`. The
UKI embeds its `IMAGE_VERSION` as the `veyra.image_version=` kernel command-line
parameter. The initrd reads that value and the dm-verity `roothash` from the
same command line and validates that the mounted verity-protected root is
Fedora 44. The roothash binds the identity to the selected root; the version
comes from the UKI itself. This avoids relying on EFI-variable availability or
mounting the ESP in the initrd.

## Trial marker shared with OTA

The system-generation trial uses one Vpkg-owned marker:
`/var/lib/veyra/vpkg/system-trial.json`. This is separate from OTA's
`pending.json` and pair-keyed deployment bindings. The marker is mode `0600`,
root-owned, atomically replaced, and protected by the Vpkg transaction lock
when written by the host CLI.

Schema 1 fields:

```json
{
  "schema": 1,
  "kind": "system-generation",
  "roothash": "<64 lowercase hexadecimal characters>",
  "version": "<UKI IMAGE_VERSION>",
  "generation_id": "generation-<32 lowercase hexadecimal characters>",
  "parent_generation": "generation-<32 lowercase hexadecimal characters>",
  "origin_boot_id": "<canonical UUID>",
  "attempts": 0,
  "state": "pending"
}
```

Allowed states are `pending`, `committing`, and `failed`. Only the exact identity
pair can select a pending candidate. The same boot that wrote the marker keeps
using `active`. The next boot increments `attempts` and mounts the candidate
without changing `active`. If that candidate fails before the boot-health
commit, a following boot marks it failed and mounts the previous `active`
generation. The post-boot health service first durably changes the marker to
`committing`, then updates the generation manifest and atomically replaces the
`active` symlink, then removes the marker. Initrd recovery finishes a partially
committed switch. Rollback selects the active generation's parent.

Vpkg mutations are rejected while an OTA update or a pending system-generation
trial exists. OTA `prepare-for-base` must reject while `system-trial.json` is
`pending` or `committing`, preserving the existing active deployment and
generation. A `failed` marker blocks new package mutations until explicitly
cleared after confirming that the old active generation remains selected. OTA
request and pin records remain owned by the existing intent API and are
re-resolved against the OTA candidate base; the marker does not replace or
rewrite those records.

## Alternatives and tradeoffs

- **Full copy of merged `/usr` and `/etc`:** simple to reason about and
  independent of OverlayFS whiteout encoding, but duplicates the complete
  system payload into `/var` for every generation. Rejected for normal staging;
  the existing ext4 partition has no dedicated capacity guarantee.
- **Copying a prior OverlayFS upper directory:** cheap for regular files but
  cannot safely preserve device-node whiteouts and may dereference payload
  symlinks. Rejected; generations retain parent upper directories as lower
  layers instead.
- **Btrfs snapshots or a new state partition:** not available in the current
  image layout and explicitly outside the approved storage design.
- **systemd-sysext:** remains the app-layer mechanism. It does not provide
  generation-owned `/etc` or a base-system RPM database and is not a substitute
  for this native generation path.

The parent-layer chain grows with each committed transaction. The implementation
must cap or compact the chain before claiming long-term support. Space remains
dynamic and can be exhausted; candidates must fail without changing the old
selector. Package scriptlets and triggers must remain disabled or otherwise
confined by a separately validated policy; this prototype does not establish
that arbitrary RPM hooks are safe to execute.

## Current implementation boundary

The current source wires `vpkg system install/remove/upgrade/rollback` to a
libdnf5 transaction in candidate `/usr` and `/etc` overlays, uses a separate
generation-owned RPM database, rejects package hooks and unsafe payloads, and
shares the existing OTA transaction lock. The initrd selects the candidate,
mounts generation overlays before `switch-root`, and falls back or commits it
after the Veyra boot-health checks. Before sealing a candidate,
`vpkg system install/remove/upgrade` prints the affected package names and asks
for interactive confirmation. A non-interactive caller must pass `--yes`; this
only confirms the reboot-required operation and does not bypass protected
package, signature, or transaction-policy checks. The previous generation
remains selected until the rebooted candidate passes boot-health checks. Source
tests cover the confirmation behavior, state transitions, and policy helpers.

This remains an implementation prototype, not a supported package workflow.
The CI and release gates verify the generated initrd contains the executable
helper, service, and required systemd enablement link. They do not yet run a
native `vpkg system` transaction in a booted guest. Fedora 44 QEMU/KVM evidence
is still required for signed RPM install/remove, the generation-owned RPM DB,
actual EFI and OverlayFS mounts, candidate reboot and health commit, failed
candidate fallback, rollback, and conflict handling with a pending OTA update.
The parent-layer chain also has no compaction/length policy, and storage
capacity can be exhausted over repeated generations.
