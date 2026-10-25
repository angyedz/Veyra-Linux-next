#!/usr/bin/bash
set -euo pipefail

virtualizer="$(systemd-detect-virt --vm || true)"
case "$virtualizer" in
  qemu|kvm) ;;
  *)
    echo "Veyra tests must run inside QEMU/KVM; detected '${virtualizer:-none}'." >&2
    exit 2
    ;;
esac

if (( EUID == 0 )); then
  echo "Run Vforge tests as a normal guest user so Podman stays rootless." >&2
  exit 2
fi

cd "$(dirname "$0")/.."
repo_root="$PWD"
export PATH="$repo_root/mkosi.extra/usr/bin:$PATH"
mount_options="$(findmnt -n -o OPTIONS --target "$repo_root")"
case ",$mount_options," in
  *,ro,*) ;;
  *)
    echo "Mount the source checkout read-only inside the QEMU guest." >&2
    exit 2
    ;;
esac

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_vforge.py'
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test-vforge-integration-qemu.py
