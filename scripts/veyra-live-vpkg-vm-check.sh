#!/usr/bin/bash
# Offline, disposable, QEMU-only Live vpkg verification. Never part of A/B.
set -euo pipefail
exec > /dev/ttyS0 2>&1
fail() {
    printf 'VEYRA_LIVE_VPKG_CI_FAILURE line=%d: %s\n' "$1" "$2"
    exit 1
}
trap 'fail "$LINENO" "$BASH_COMMAND"' ERR
[[ -f /etc/veyra/live-mode ]] || exit 0
[[ "$(< /etc/veyra/live-mode)" == "veyra-next-native-live-iso" ]] || exit 0
case "$(systemd-detect-virt --vm || :)" in
    qemu|kvm) ;;
    *) exit 0 ;;
esac
echo 'VEYRA_LIVE_VPKG_CI_BEGIN'
/usr/bin/vpkg --help >/dev/null
/usr/bin/runuser -u liveuser -- /usr/local/bin/vpkg doctor
/usr/bin/python3 -c 'import libdnf5; import sys; assert sys.version_info.major == 3'
test -s /usr/share/veyra/sysext-level
test -s /usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-free-fedora-2020
test -s /usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-nonfree-fedora-2020
/usr/bin/vpkg channel
/usr/bin/python3 -m unittest discover \
    --start-directory /usr/share/veyra/vpkg-selftest/tests \
    --pattern test_vpkg.py -q

# sudo policy must reject arbitrary root commands, and vpkg must not
# permit kernel/base package installation even under the allowed install verb.
if /usr/bin/runuser -u liveuser -- /usr/bin/sudo -n /usr/bin/id -u; then
    fail "$LINENO" 'liveuser can execute an unrelated root command'
fi
if /usr/bin/runuser -u liveuser -- /usr/bin/sudo -n /usr/bin/vpkg remove firefox; then
    fail "$LINENO" 'liveuser can remove packages via unapproved sudo'
fi
log="$(mktemp /run/veyra-vpkg-live-reject.XXXXXXXX)"
if /usr/bin/runuser -u liveuser -- /usr/local/bin/vpkg install kernel-core > "$log" 2>&1; then
    cat "$log"
    fail "$LINENO" 'vpkg unexpectedly allowed a protected kernel RPM'
fi
grep -Fq 'REJECT: refusing kernel-core' "$log" || {
    cat "$log"
    fail "$LINENO" 'kernel rejection did not pass through vpkg policy'
}
rm -f "$log"
echo 'VEYRA_LIVE_VPKG_CI_PASS (offline policy + 58 Python tests; RPM transaction not tested)'
