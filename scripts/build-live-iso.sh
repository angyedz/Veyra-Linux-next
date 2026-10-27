#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
boot_iso="${VEYRA_FEDORA_BOOT_ISO:?set VEYRA_FEDORA_BOOT_ISO to the verified Fedora 44 netinst ISO}"
workspace="${GITHUB_WORKSPACE:-$root}"
result_dir="$root/build/live-media"
output_dir="$root/build/live-iso"
kickstart="$root/build/veyra-live.ks"
log_file="$root/build/livemedia.log"
max_bytes=$((8 * 1024 * 1024 * 1024))

[[ -s "$boot_iso" ]] || { echo "Fedora 44 netinst ISO is missing: $boot_iso" >&2; exit 1; }
[[ -s "$root/build/recovery-uki/veyra-recovery.efi" ]] || {
  echo "Recovery UKI artifact is missing." >&2
  exit 1
}

rm -rf -- "$result_dir" "$output_dir" "$log_file"
mkdir -p "$output_dir" "$(dirname -- "$kickstart")"
# Render the kickstart for every attempt; retry against a different Fedora endpoint.
render_kickstart() {
  local repo_url="$1"
  sed -e "s|__VEYRA_SOURCE_ROOT__|$workspace|g" \
      -e "s|__VEYRA_FEDORA_REPO_URL__|$repo_url|g" \
      "$root/installer/veyra-live.ks.in" > "$kickstart"
}
repo_primary="https://download.fedoraproject.org/pub/fedora/linux/releases/44/Everything/x86_64/os/"
repo_fallback="https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Everything/x86_64/os/"

# Repeat only failures that explicitly report a package/mirror download issue.
# Keep every failed attempt log to distinguish mirror failures from ISO bugs.
max_attempts="${VEYRA_ISO_MAX_ATTEMPTS:-2}"
if [[ ! "$max_attempts" =~ ^[12]$ ]]; then
  echo "VEYRA_ISO_MAX_ATTEMPTS must be 1 or 2." >&2
  exit 2
fi
attempt_logs="$root/build/iso-attempt-logs"
rm -rf -- "$attempt_logs"
mkdir -p "$attempt_logs"
built=0
for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  repo_url="$repo_primary"
  if ((attempt > 1)); then
    repo_url="$repo_fallback"
  fi
  render_kickstart "$repo_url"
  echo "Building Veyra Live ISO: Lorax attempt $attempt/$max_attempts (repo: $repo_url)" >&2
  if livemedia-creator \
    --make-iso \
    --no-virt \
    --iso="$boot_iso" \
    --ks="$kickstart" \
    --releasever=44 \
    --resultdir="$result_dir" \
    --logfile="$log_file" \
    --iso-only \
    --iso-name=veyra-next-prototype-live.iso \
    --extra-boot-args="console=ttyS0,115200n8" 2>&1 | tee "$attempt_logs/livemedia-attempt-$attempt-console.log"; then
    built=1
    break
  fi

  # Lorax's generic log sometimes only says "Failed to download packages".
  # Preserve the underlying Anaconda packaging logs on the disposable CI job
  # to identify which repository or RPM actually failed.
  attempt_details="$attempt_logs/attempt-$attempt"
  mkdir -p "$attempt_details"
  if [[ -f "$log_file" ]]; then
    cp -- "$log_file" "$attempt_logs/livemedia-attempt-$attempt.log"
  fi
  for install_log in \
      /tmp/anaconda.log /tmp/packaging.log /tmp/dnf.log /tmp/program.log \
      /var/log/anaconda/anaconda.log /var/log/anaconda/packaging.log \
      /var/log/anaconda/dnf.log /var/log/anaconda/program.log; do
    if [[ -f "$install_log" && ! -L "$install_log" ]]; then
      safe_name="${install_log#/}"
      safe_name="${safe_name//\//_}"
      # Bound GitHub artifact sizes; never archive the entire build root.
      tail -c 1048576 -- "$install_log" > "$attempt_details/$safe_name" || true
    fi
  done
  if ((attempt >= max_attempts)); then
    echo "Live ISO build failed after $attempt attempts; see $attempt_logs." >&2
    exit 1
  fi
  download_error='failed to download (the following )?packages|failed to download metadata|curl error|cannot download repomd'
  if ! { grep -Eqi "$download_error" "$log_file" 2>/dev/null ||
         grep -Eqi "$download_error" "$attempt_logs/livemedia-attempt-$attempt-console.log"; }; then
    echo "Non-download Lorax error; will not retry. See $attempt_logs." >&2
    exit 1
  fi
  echo "Package download failed; retrying the disposable Lorax build once." >&2
  rm -rf -- "$result_dir"
  rm -f -- "$log_file"
  sleep 5
done
if ((built != 1)); then
  echo "Live ISO builder never produced a successful result." >&2
  exit 1
fi

mapfile -t images < <(find "$result_dir" -type f -name 'veyra-next-prototype-live.iso' -print)
if ((${#images[@]} != 1)) || [[ ! -s "${images[0]:-}" ]]; then
  echo "Expected one non-empty Live Installer ISO; found ${#images[@]}." >&2
  find "$result_dir" -maxdepth 3 -type f -printf '%p (%s bytes)\n' >&2
  exit 1
fi

iso="$output_dir/veyra-next-prototype-live.iso"
cp --reflink=auto --sparse=always "${images[0]}" "$iso"
size="$(stat --format=%s "$iso")"
if ((size > max_bytes)); then
  echo "Live Installer ISO is $size bytes, over the 8 GiB limit." >&2
  exit 1
fi

xorriso -indev "$iso" -report_el_torito as_mkisofs > "$output_dir/eltorito.txt"
grep -Eq '(^|[[:space:]])-b[[:space:]]' "$output_dir/eltorito.txt" || {
  echo "ISO has no Legacy BIOS El Torito boot entry." >&2
  exit 1
}
grep -Eq '(^|[[:space:]])-e[[:space:]]' "$output_dir/eltorito.txt" || {
  echo "ISO has no UEFI El Torito boot entry." >&2
  exit 1
}
(
  cd "$output_dir"
  sha256sum "$(basename -- "$iso")" > "$(basename -- "$iso").sha256"
)
rm -rf -- "$result_dir" "$kickstart"
printf 'Built one Live Installer ISO: %s (%s bytes)\n' "$iso" "$size"
