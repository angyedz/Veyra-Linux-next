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
python3 -m unittest discover -s tests -p 'test_vforge.py'
