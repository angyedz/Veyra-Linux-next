#!/usr/bin/env bash
# Export only public trust keys inside an ephemeral, reviewed-main composer.
set -euo pipefail
[[ -n "${VEYRA_UPDATE_SIGNING_KEY:-}" && -n "${VEYRA_RPM_SIGNING_KEY:-}" ]] || {
  echo "Both dedicated Veyra signing secrets must be provisioned." >&2
  exit 1
}
repo="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
work="$(mktemp -d)"
chmod 0700 "$work"
trap 'rm -rf -- "$work"' EXIT

export_one() {
  local source="$1" flavor="$2" destination="$3" fingerprint
  export GNUPGHOME="$work/$flavor"
  install -d -m 0700 "$GNUPGHOME"
  printf '%s' "$source" | gpg --batch --quiet --import 2>/dev/null
  mapfile -t fingerprints < <(gpg --batch --with-colons --list-secret-keys |
    awk -F: '$1 == "sec" { primary=1; next } $1 == "ssb" { primary=0 } $1 == "fpr" && primary { print $10; primary=0 }')
  [[ "${#fingerprints[@]}" -eq 1 ]] || {
    echo "$flavor input must contain exactly one private primary key." >&2
    exit 1
  }
  fingerprint="${fingerprints[0]}"
  install -d -m 0755 "$(dirname "$destination")"
  if [[ "$flavor" == update ]]; then
    gpg --batch --export "$fingerprint" > "$destination"
  else
    gpg --batch --armor --export "$fingerprint" > "$destination"
  fi
  [[ -s "$destination" ]] || {
    echo "Could not export pinned $flavor public key." >&2
    exit 1
  }
  chmod 0644 "$destination"
  unset GNUPGHOME
}
export_one "$VEYRA_UPDATE_SIGNING_KEY" update \
  "$repo/mkosi.extra/usr/lib/systemd/import-pubring.pgp"
export_one "$VEYRA_RPM_SIGNING_KEY" rpm \
  "$repo/mkosi.extra/usr/share/veyra/RPM-GPG-KEY-VEYRA"
echo 'Pinned public update and RPM trust roots exported into ISO staging.'
