# Vforge snapshots and base compatibility

## Status

Accepted

## Decision

Vforge keeps environment state and snapshots in the storage location selected
by the user. It does not create a separate filesystem partition or reserve a
fixed amount of space. A snapshot copies the persistent OverlayFS upper and
records the environment identity plus the active Veyra image ID, version, and
root digest. Reflinks are used when the backing filesystem supports them;
otherwise the copy uses ordinary files.

Environment entry binds the environment to the active Veyra base on first use.
When OTA changes the base, Vforge blocks the environment until
`vforge upgrade NAME` snapshots its state, runs `vpkg upgrade` on the new base,
and records the new binding. Failure restores the saved upper and keeps the old
base binding. Snapshot rollback is allowed only when the snapshot's Veyra base
is active; to restore a snapshot from an older base, the system deployment
must first be rolled back.

Vforge uses shared session locks and exclusive maintenance locks. Snapshot,
rollback, upgrade, and deletion refuse to run while an environment has an
active session.

## Consequences

- Users choose where shared environments live; project environments remain in
  the project's `.vforge/` directory.
- Storage use follows the actual upper-layer size and available reflink support.
- Old-base snapshots remain available for a later system rollback, but Vforge
  will not mount them against a different system base.
- First release validation must include snapshot restore and OTA base changes
  in a Fedora 44 Veyra QEMU guest.
