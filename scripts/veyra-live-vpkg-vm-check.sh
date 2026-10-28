#!/usr/bin/bash
# Offline, disposable, QEMU-only Live vpkg verification. Never part of A/B.
set -euo pipefail
# Keep the test output separate from the serial-getty, which emits terminal
# escape sequences into ttyS0 and can hide stderr/tracebacks mid-test.
log_file=/run/veyra-live-vpkg-vm-check.log
exec > "$log_file" 2>&1
last_step=initialization
report_exit() {
    status=$?
    trap - EXIT
    if (( status != 0 )); then
        printf 'VEYRA_LIVE_VPKG_CI_FAILURE status=%s step=%s\n' "$status" "$last_step"
        printf 'VEYRA_LIVE_VPKG_CI_FAILURE status=%s step=%s\n' "$status" "$last_step" >/dev/kmsg || true
    fi
    # Journal captures all output even when agetty resets the serial TTY.
    /usr/bin/systemd-cat -t veyra-live-vpkg-selftest < "$log_file" || true
    if [[ -c /dev/ttyS0 ]]; then
        cat "$log_file" > /dev/ttyS0 || true
    fi
}
trap report_exit EXIT
step() {
    last_step="$1"
    printf 'VEYRA_LIVE_VPKG_CI_STEP %s\n' "$last_step"
}
fail() {
    printf 'VEYRA_LIVE_VPKG_CI_FAILURE line=%d step=%s: %s\n' "$1" "$last_step" "$2"
    return 1
}
[[ -f /etc/veyra/live-mode ]] || exit 0
[[ "$(< /etc/veyra/live-mode)" == "veyra-next-native-live-iso" ]] || exit 0
case "$(systemd-detect-virt --vm || :)" in
    qemu|kvm) ;;
    *) exit 0 ;;
esac
echo 'VEYRA_LIVE_VPKG_CI_BEGIN'
step vpkg-cli
/usr/bin/vpkg --help >/dev/null
step unprivileged-doctor
/usr/bin/runuser -u liveuser -- /usr/local/bin/vpkg doctor
/usr/bin/python3 -c 'import libdnf5; import sys; assert sys.version_info.major == 3'
test -s /usr/share/veyra/sysext-level
test -s /usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-free-fedora-2020
test -s /usr/share/pki/rpm-gpg/RPM-GPG-KEY-rpmfusion-nonfree-fedora-2020
/usr/bin/vpkg channel
step python-policy-suite
/usr/bin/python3 -m unittest discover \
    --start-directory /usr/share/veyra/vpkg-selftest/tests \
    --pattern test_vpkg.py -q

# sudo policy must reject arbitrary root commands, and vpkg must not
# permit kernel/base package installation even under the allowed install verb.
step sudo-policy
if /usr/bin/runuser -u liveuser -- /usr/bin/sudo -n /usr/bin/id -u; then
    fail "$LINENO" 'liveuser can execute an unrelated root command'
fi
if /usr/bin/runuser -u liveuser -- /usr/bin/sudo -n /usr/bin/vpkg remove firefox; then
    fail "$LINENO" 'liveuser can remove packages via unapproved sudo'
fi
step blocked-kernel
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

# The Live ISO may intentionally omit Recovery, but the installer preflight
# must fail closed in that mode. If a verified Recovery UKI was bundled,
# require a successful read-only preflight instead. No target disk is used.
step installer-preflight
recovery=/usr/share/veyra/installer/source/assets/veyra-recovery.efi
install_log="$(mktemp /run/veyra-installer-preflight.XXXXXXXX)"
if [[ -f "$recovery" || -L "$recovery" ]]; then
    if ! /usr/libexec/veyra-installer --preflight > "$install_log" 2>&1; then
        cat "$install_log"
        fail "$LINENO" 'Live installer rejected its bundled Recovery UKI during preflight'
    fi
    echo 'VEYRA_LIVE_INSTALLER_PREFLIGHT_PASS recovery=present'
else
    if /usr/libexec/veyra-installer --preflight > "$install_log" 2>&1; then
        cat "$install_log"
        fail "$LINENO" 'Live installer accepted installation without a Recovery UKI'
    fi
    grep -Fq 'Recovery UKI' "$install_log" || {
        cat "$install_log"
        fail "$LINENO" 'Live installer failed preflight for an unexpected reason'
    }
    echo 'VEYRA_LIVE_INSTALLER_PREFLIGHT_PASS recovery=absent-rejected'
fi
rm -f "$install_log"
step completed
echo 'VEYRA_LIVE_VPKG_CI_PASS (offline policy + installer preflight; RPM transaction not tested)'
