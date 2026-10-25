#!/usr/bin/env bash
set -euo pipefail

if [[ "${CI:-}" != "true" || "${VEYRA_PACKAGE_CI_TEST:-}" != "1" ]]; then
  echo "This integration test is only allowed in the disposable package CI container." >&2
  exit 2
fi
if (( EUID != 0 )); then
  echo "Run this package transaction test as root inside the package CI container." >&2
  exit 2
fi
if (( $# != 1 )); then
  echo "Usage: $0 <signed-package-site-directory>" >&2
  exit 2
fi

site="$(realpath "$1")"
key="$site/RPM-GPG-KEY-TEST"
test -s "$key"
test -s "$site/packages/testing/x86_64/repodata/repomd.xml"
test -s "$site/packages/testing/x86_64/repodata/repomd.xml.asc"
test -s "$site/packages/main/x86_64/repodata/repomd.xml"

install_root=/var/lib/veyra/vpkg/root
channel_file=/etc/veyra/vpkg/channel
extension_link=/etc/extensions/veyra-shell-tools.raw
extension_image="$install_root/var/lib/extensions/veyra-shell-tools.raw"
if [[ -e "$install_root" || -e "$channel_file" || -e "$extension_link" || -L "$extension_link" ]]; then
  echo "Refusing to reuse Veyra package state in the CI container." >&2
  exit 2
fi

cat > /etc/dnf/dnf.conf <<'EOF'
[main]
assumeyes=True
EOF
cat > /etc/yum.repos.d/veyra-ci.repo <<EOF
[veyra-main]
name=Veyra main package CI
baseurl=file://$site/packages/main/x86_64/
enabled=0
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://$key

[veyra-testing]
name=Veyra testing package CI
baseurl=file://$site/packages/testing/x86_64/
enabled=0
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://$key
EOF

mkdir -p "$install_root" /run/veyra-vpkg-ci /etc/extensions
rpm --import "$key"
rpm --root "$install_root" --initdb
rpm --root "$install_root" --import "$key"

shim=/run/veyra-vpkg-ci/bin
mkdir -p "$shim"
cat > "$shim/systemd-sysext" <<'EOF'
#!/usr/bin/bash
set -euo pipefail
if [[ "${1:-}" != "refresh" || $# != 1 ]]; then
  echo "unexpected systemd-sysext test invocation" >&2
  exit 2
fi
marker=/run/veyra-vpkg-ci/failed-first-refresh
if [[ "${VEYRA_CI_FAIL_REFRESH_ONCE:-0}" == "1" && ! -e "$marker" ]]; then
  touch "$marker"
  exit 1
fi
EOF
chmod 0755 "$shim/systemd-sysext"
vpkg="$PWD/mkosi.extra/usr/bin/vpkg"
log=/run/veyra-vpkg-ci/vpkg-failed-install.log

"$vpkg" channel testing
if VEYRA_CI_FAIL_REFRESH_ONCE=1 PATH="$shim:$PATH" "$vpkg" install veyra-shell-tools >"$log" 2>&1; then
  cat "$log"
  echo "vpkg unexpectedly reported success after system-extension activation failed." >&2
  exit 1
fi
cat "$log"
grep -Fq "The RPM transaction was rolled back." "$log"
if rpm --root "$install_root" -q veyra-shell-tools >/dev/null 2>&1; then
  echo "vpkg left the RPM installed after the activation failure." >&2
  exit 1
fi
if [[ -e "$extension_image" || -L "$extension_link" ]]; then
  echo "vpkg left an extension image or host link after the activation failure." >&2
  exit 1
fi

PATH="$shim:$PATH" "$vpkg" install veyra-shell-tools
rpm --root "$install_root" -q veyra-shell-tools
test -s "$extension_image"
test -L "$extension_link"
test "$(readlink -f "$extension_link")" = "$(realpath "$extension_image")"
PATH="$shim:$PATH" "$vpkg" list | grep -Fq veyra-shell-tools
PATH="$shim:$PATH" "$vpkg" info veyra-shell-tools | grep -Fq veyra-shell-tools
PATH="$shim:$PATH" "$vpkg" upgrade

"$vpkg" channel main
available="$(PATH="$shim:$PATH" "$vpkg" list --available)"
if grep -Fq veyra-shell-tools <<<"$available"; then
  echo "vpkg exposed a testing-only RPM in the empty main channel." >&2
  exit 1
fi

"$vpkg" channel testing
PATH="$shim:$PATH" "$vpkg" remove veyra-shell-tools
if rpm --root "$install_root" -q veyra-shell-tools >/dev/null 2>&1; then
  echo "vpkg did not remove the installed extension RPM." >&2
  exit 1
fi
if [[ -e "$extension_link" || -L "$extension_link" ]]; then
  echo "vpkg left the extension link after package removal." >&2
  exit 1
fi
if rpm -q veyra-shell-tools >/dev/null 2>&1; then
  echo "vpkg modified the host Fedora RPM database." >&2
  exit 1
fi

echo "vpkg install, channel isolation, no-op upgrade, remove, and activation-failure rollback passed."
