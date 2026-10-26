# Veyra Next OTA transport: chunked delta prototype

**Status:** offline transport prototype only; not integrated with the active A/B updater. This document is intentionally separate from ongoing OTA feature PRs.

## Release artifacts and GitHub limit

GitHub Releases allows up to 1,000 assets per release, with each file under
2 GiB ([GitHub documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)).
The prototype keeps a 2,000,000,000-byte absolute file ceiling. Each image
chunk is independently compressed and capped at **4 MiB + 64 KiB**. The
central release builder places those chunks into deterministic tar packs capped
at **256 MiB of chunk data**, **257 MiB per pack**, and **128 members per pack**.
Packed manifests use schema v2; the downloader fetches and resumes a pack once
even when many chunks refer to it. Reconstruction validates the pack digest,
member names, member sizes, and each decompressed chunk. The builder counts
`release.json`, manifests, and packs, then refuses output above GitHub's
1,000-asset release limit. An ISO is **not** an OTA payload.

Release assets should eventually contain:
- a small **signed** manifest identifying target version, exact base version/image digest, target root digest, chunk hashes, artifact sizes and signatures;
- content-addressed compressed changed chunks (unchanged chunks are reused from the existing base);
- a complete fallback root image split into individually bounded assets when needed, **not** the installer ISO;
- matching UKI, dm-verity metadata, and any other resources needed for one coherent A/B generation.

The current `scripts/ota_chunks.py` emits a **non-signed JSON manifest** and
independently compressed zlib chunks. `scripts/veyra-release.py` converts those
into bounded packs, verifies full and delta reconstruction, and emits one flat
directory for upload. It does not publish the release or implement signatures,
verity, UKI, slot activation, or rollback. Do not distribute it as a
production update.

## Atomic installation requirements

1. Verify signed release metadata and its rollback/version policy **before** trusting any chunk list.
2. Download chunks into a bounded staging area, verify sizes and SHA-256, and resume by content digest. Limit decompression, file count, and disk use.
3. Reconstruct only the **inactive** system slot from a verified matching base, never modify the running slot. If the base does not match, fetch a full fallback.
4. Verify the entire target slot digest and dm-verity tree; install matching signed UKI and boot resources as one release generation.
5. Atomically stage the trial boot. Mark healthy only after successful boot and required services; otherwise retain the prior slot.
6. Keep `/home` and other persistent user data outside the slot and define migration/rollback compatibility separately.

## Efficiency and limitations

This initial prototype compares fixed 4 MiB blocks at equal offsets and compresses changed blocks individually. That is simple and bounded, but **shifting data changes many subsequent blocks**, so real EROFS image rebuilds may generate poor deltas. Measure actual release-to-release reuse before choosing between fixed blocks, content-defined chunking, `zstd --patch-from`, or a dedicated delta engine. Never assume compression alone guarantees small OTA downloads.

Example offline experiment:

```sh
python3 scripts/ota_chunks.py next-root.img ./bundle --base current-root.img
python3 -m unittest discover -s tests -p 'test_ota_chunks.py' -v
```

Before production, test interrupted downloads, power loss at every stage, corrupted chunks, rollback, mixed-version boot assets, constrained disk, and GitHub release asset publication limits.

## Transfer-size benchmark and fallback policy

Use `python3 scripts/benchmark_ota.py current-root.img next-root.img ./bundle`
to measure the *actual compressed changed assets* plus manifest against a full
zlib-compressed target. It writes `benchmark.json` and recommends `delta`
only when estimated transfer is less than **85%** of full compressed transfer.
Otherwise it recommends `full` (this prototype does not package or install
that fallback). The estimate does not include HTTP overhead, signatures, UKI
or dm-verity artifacts, and must not be mistaken for production download size.

Fixed-offset chunks are particularly weak when filesystem image offsets shift.
Benchmark real consecutive EROFS builds before adopting this as the OTA format.

## Centralized experimental release builder

One command packages both a complete fallback and an optional delta:

```sh
python3 scripts/veyra-release.py \\
  --version 0.2.0 --base-version 0.1.0 \\
  --base ./old-root.img --target ./new-root.img \\
  --output ./release-0.2.0
```

For the first release, omit `--base` and `--base-version`.
The output is a flat directory of GitHub-uploadable individual files:
`release.json`, `ota-full.json`, optional `ota-delta.json`, and
content-addressed `chunks-*.tar` packs. The builder reconstructs and checks
both variants **before** publishing the output directory, deduplicates shared
chunks, caps each pack, and rejects releases that exceed the configured file
size or GitHub's asset-count limit.

**Critical:** this is an UNSIGNED EXPERIMENTAL artifact bundle. The
`release.json` marker explicitly says NOT INSTALLABLE. Do not attach it to
a production updater or publish as an official secure update. Signing,
release channels, GitHub upload automation, UKI/verity artifacts and A/B
activation belong to a later integration step with the OTA owner.
