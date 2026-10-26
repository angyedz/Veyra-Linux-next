# Visual QA on a disposable QEMU guest

The system image is an **experimental Fedora disk image**, not an ISO.
First boot uses KDE Plasma Setup, so a boot screenshot is *not* the same as a
logged-in Plasma desktop screenshot. The guest should not be considered
visually validated until a tester completes first-run setup and logs in.

## Local diagnostic captures

Linux packages: qemu-system-x86_64, OVMF (code and variables firmware),
Python 3. Linux host only; no root required.

Build the current branch, then:

```sh
scripts/mkosi.sh --force build
python3 scripts/qemu-glacier-capture.py \
  --image mkosi.output/veyra-next.raw \
  --output qemu-glacier-visual-report \
  --at 25,80
```

The runner starts headless QEMU with a VGA display, UEFI pflash,
**a copied OVMF variable image**, and a **snapshot-only guest drive**.
It never writes to the host bootloader or the original `.raw`.
The QEMU monitor captures two VGA screenshots and converts QEMU PPMs
to standard PNGs using Python's standard library. Output includes
serial diagnostics, QEMU stderr and a metadata file. Guest screenshots
may contain bootloader, Plasma Setup, login manager, desktop or failures;
they are artifacts for human review, **not automatic proof of success**.

It deliberately disables VM networking. Its QEMU engine is TCG software
emulation so the test works without nested virtualization, but timing
cannot be used as a real boot performance metric.

Existing report directories and symlinks are rejected (no overwriting).
Suggested snapshot times on slow shared runners are 25s and 80s;
longer runs are configurable up to 300s.

## GitHub Actions diagnostic

Workflow: **Veyra Glacier visual QEMU capture (manual)**. Select the
theme feature branch and trigger `workflow_dispatch`. It builds a
fresh image, captures guest screenshots, and uploads an artifact even if
the capture job errors.

Once a human completes first boot in an interactive disposable QEMU,
the acceptance checklist is:

- Dark and Light global themes appear in System Settings.
- Preview images are rendered; panels and applets render without
  missing shapes, odd borders or clipped focus states.
- `veyra-theme light` followed by `veyra-theme dark` changes colors,
  Plasma style and KDE icons without requiring root.
- The wallpaper package works in both modes; manual user wallpaper
  changes remain respected.
- Titlebar controls are **on the right**, ordered Minimize, Maximize,
  Close (no macOS dots).
- Test scaling at 100%, 125%, 200% and keyboard-only interaction.
- Record actual screenshots and `journalctl --user -b` warnings.
- Only remove the PR's Draft status after these visual checks and
  full CI validation. CI screenshots alone never prove successful login.
