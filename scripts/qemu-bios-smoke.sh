#!/usr/bin/env bash
# Diagnostic ONLY: boot a disk image under SeaBIOS (legacy BIOS).
# A UEFI-only image is expected to fail until GRUB i386-pc is installed.
set -euo pipefail
image="${1:-mkosi.output/veyra-next.raw}"
report="${2:-qemu-bios-report}"
seconds="${VEYRA_QEMU_TIMEOUT:-90}"
[[ -s "$image" ]] || { echo "Image missing: $image" >&2; exit 2; }
[[ "$seconds" =~ ^[0-9]+$ ]] && (( seconds >= 10 && seconds <= 600 )) || {
    echo "Timeout must be 10-600 seconds" >&2; exit 2;
}
[[ ! -e "$report" && ! -L "$report" ]] || { echo "Report already exists" >&2; exit 2; }
command -v qemu-system-x86_64 >/dev/null
command -v timeout >/dev/null
mkdir -m 700 "$report"
set +e
timeout --signal=TERM --kill-after=5s "${seconds}s" qemu-system-x86_64 \
    -machine pc,accel=tcg -cpu max -smp 2 -m 2048 \
    -display none -monitor none -no-reboot \
    -drive "if=virtio,format=raw,snapshot=on,file=$image" \
    -serial "file:$report/serial.log" \
    >"$report/qemu.stdout" 2>"$report/qemu.stderr"
status=$?
set -e
printf 'firmware=SeaBIOS\nexit_code=%s\n' "$status" >"$report/metadata.txt"
echo "Legacy BIOS diagnostic: $report (exit $status). A timeout is NOT a successful boot."
if [[ "$status" -ne 0 && "$status" -ne 124 ]]; then exit "$status"; fi
