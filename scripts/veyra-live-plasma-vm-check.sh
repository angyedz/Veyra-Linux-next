#!/usr/bin/bash
# QEMU-only readiness probe for both parts of the interactive KDE session.
set -euo pipefail
[[ -f /etc/veyra/live-mode ]] || exit 0
[[ "$(< /etc/veyra/live-mode)" == "veyra-next-native-live-iso" ]] || exit 0
case "$(systemd-detect-virt --vm 2>/dev/null || :)" in
  qemu|kvm) ;;
  *) exit 0 ;;
esac
# A login manager or a visible cursor isn't proof that KDE works.
for attempt in {1..18}; do
  if /usr/bin/pgrep -u liveuser -x kwin_wayland >/dev/null &&
     /usr/bin/pgrep -u liveuser -x plasmashell >/dev/null; then
    printf 'VEYRA_LIVE_PLASMA_PROCESS_PASS\n' >/dev/ttyS0
    exit 0
  fi
  sleep 5
done
printf 'VEYRA_LIVE_PLASMA_PROCESS_FAILURE: missing kwin_wayland or plasmashell\n' >/dev/ttyS0
exit 1
