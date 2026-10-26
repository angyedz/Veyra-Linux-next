#!/usr/bin/env bash
# QEMU-only diagnostics: ISO is read-only; UEFI variables are copied locally.
set -euo pipefail

if (( $# < 2 || $# > 3 )); then
  echo "Usage: qemu-live-iso-smoke.sh ISO REPORT_DIR [bios|uefi|both]" >&2
  exit 2
fi
iso="$1"
report="$2"
mode="both"
if (( $# == 3 )); then mode="$3"; fi
seconds="$(printenv VEYRA_QEMU_BOOT_SECONDS || printf 240)"
[[ "$mode" == bios || "$mode" == uefi || "$mode" == both ]] || { echo "Invalid mode" >&2; exit 2; }
[[ "$seconds" =~ ^[0-9]+$ ]] && (( seconds >= 90 && seconds <= 900 )) || { echo "Timeout must be 90-900 seconds" >&2; exit 2; }
[[ -f "$iso" && -s "$iso" && ! -L "$iso" ]] || { echo "Missing/invalid ISO" >&2; exit 2; }
[[ ! -e "$report" && ! -L "$report" ]] || { echo "Report already exists" >&2; exit 2; }
for tool in timeout qemu-system-x86_64 xorriso; do command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 2; }; done

iso="$(realpath -- "$iso")"
eltorito="$(xorriso -indev "$iso" -report_el_torito as_mkisofs 2>/dev/null)"
grep -Eq '(^|[[:space:]])-b[[:space:]]' <<< "$eltorito" || { echo "BIOS ISO boot entry absent" >&2; exit 2; }
grep -Eq '(^|[[:space:]])-e[[:space:]]' <<< "$eltorito" || { echo "UEFI ISO boot entry absent" >&2; exit 2; }
code=""
vars=""
if [[ "$mode" != bios ]]; then
  for prefix in /usr/share/OVMF /usr/share/edk2/x64 /usr/share/edk2/ovmf; do
    for suffix in _4M "" .4m; do
      a="$prefix/OVMF_CODE$suffix.fd"
      b="$prefix/OVMF_VARS$suffix.fd"
      if [[ -s "$a" && -s "$b" ]]; then code="$a"; vars="$b"; break 2; fi
    done
  done
  [[ -n "$code" ]] || { echo "Matched OVMF_CODE and OVMF_VARS not found" >&2; exit 2; }
fi
mkdir -m 0700 -- "$report"
printf '%s\n' "$eltorito" > "$report/eltorito.txt"
result=0
for fw in bios uefi; do
  if [[ "$mode" != both && "$mode" != "$fw" ]]; then continue; fi
  stdout="$report/$fw.stdout.log"
  stderr="$report/$fw.stderr.log"
  serial="$report/$fw.serial.log"
  set +e
  if [[ "$fw" == bios ]]; then
    timeout --signal=TERM --kill-after=5s "$seconds" qemu-system-x86_64 \
      -machine q35,accel=tcg -cpu max -smp 2 -m 4096 \
      -display none -vga std -monitor none -no-reboot -nic none \
      -boot order=d -drive "media=cdrom,readonly=on,format=raw,file=$iso" \
      -serial "file:$serial" > "$stdout" 2> "$stderr"
    status=$?
  else
    copied_vars="$report/OVMF_VARS-test.fd"
    cp -- "$vars" "$copied_vars"
    timeout --signal=TERM --kill-after=5s "$seconds" qemu-system-x86_64 \
      -machine q35,accel=tcg -cpu max -smp 2 -m 4096 \
      -display none -vga std -monitor none -no-reboot -nic none \
      -boot order=d -drive "media=cdrom,readonly=on,format=raw,file=$iso" \
      -drive "if=pflash,format=raw,unit=0,readonly=on,file=$code" \
      -drive "if=pflash,format=raw,unit=1,file=$copied_vars" \
      -serial "file:$serial" > "$stdout" 2> "$stderr"
    status=$?
  fi
  set -e
  {
    echo "firmware=$fw"
    echo "qemu_exit=$status"
    echo "duration=$seconds"
    echo "iso_readonly=true"
  } > "$report/$fw.status.txt"
  if [[ "$status" -ne 0 && "$status" -ne 124 ]]; then
    echo "QEMU error ($fw): $status" >&2
    result=1
  fi
  if grep -Eiq 'Kernel panic|VFS: Unable to mount root|Failed to mount /sysroot' "$serial"; then
    echo "Kernel/root mount failure found in $fw serial log" >&2
    result=1
  fi
  if grep -Eq 'Linux version [0-9]|systemd\[1\]:|Reached target |dracut-initqueue' "$serial"; then
    echo "Linux progress observed: $fw"
  else
    echo "UNVERIFIED: no Linux boot progress in $fw serial output"
  fi
done
echo "Diagnostics saved in $report. GUI, installer and installed system are NOT verified."
exit "$result"
