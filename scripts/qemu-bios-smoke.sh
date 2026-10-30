#!/usr/bin/env bash
# Acceptance check: boot a disk image under SeaBIOS and require a guest marker.
set -euo pipefail
image="${1:-mkosi.output/veyra-next.raw}"
report="${2:-qemu-bios-report}"
seconds="${VEYRA_QEMU_TIMEOUT:-90}"
exec python3 "$(dirname "$0")/test-bios-boot.py" \
    --image "$image" --report-dir "$report" --timeout "$seconds"
