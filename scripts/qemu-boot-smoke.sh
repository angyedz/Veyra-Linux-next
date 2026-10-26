#!/usr/bin/env bash
# Opt-in, non-destructive Veyra Next UEFI/QEMU boot smoke test.
# This is a diagnostic runner, not proof that the graphical desktop started.
set -euo pipefail

image="${1:-mkosi.output/veyra-next.raw}"
report="${2:-qemu-boot-report}"
duration="${VEYRA_QEMU_TIMEOUT:-120}"

if [[ ! -f "$image" || ! -s "$image" ]]; then
    echo "Missing or empty image: $image" >&2
    exit 2
fi
if [[ ! "$duration" =~ ^[0-9]+$ ]] || (( duration < 10 || duration > 600 )); then
    echo "VEYRA_QEMU_TIMEOUT must be between 10 and 600 seconds" >&2
    exit 2
fi
if [[ -e "$report" || -L "$report" ]]; then
    echo "Refusing existing report directory: $report" >&2
    exit 2
fi
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 missing" >&2; exit 2; }
command -v timeout >/dev/null || { echo "timeout missing" >&2; exit 2; }

firmware=""
for candidate in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd; do
    if [[ -r "$candidate" ]]; then firmware="$candidate"; break; fi
done
if [[ -z "$firmware" ]]; then
    echo "OVMF firmware not found (install ovmf)" >&2
    exit 2
fi

mkdir -m 700 "$report"
# Never boot the original image read-write. QEMU's temporary snapshot
# discards guest changes, including any attempted bootloader writes.
set +e
timeout --signal=TERM --kill-after=5s "${duration}s" \
    qemu-system-x86_64 \
    -machine q35,accel=tcg -cpu max -smp 2 -m 3072 \
    -display none -monitor none -no-reboot \
    -drive "if=pflash,format=raw,readonly=on,file=$firmware" \
    -drive "if=virtio,format=raw,snapshot=on,file=$image" \
    -serial "file:$report/serial.log" \
    >"$report/qemu.stdout" 2>"$report/qemu.stderr"
status=$?
set -e
printf 'qemu_exit_code=%s\ntimeout_seconds=%s\nfirmware=%s\n' "$status" "$duration" "$firmware" >"$report/metadata.txt"
echo "QEMU diagnostic report: $report (exit $status)"
# Timeout is expected for a running guest; it does not prove a boot succeeded.
if [[ "$status" -ne 0 && "$status" -ne 124 ]]; then
    exit "$status"
fi
echo "Inspect serial.log and guest display separately before claiming a successful boot."
