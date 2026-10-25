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
if [[ "${1:-}" == "status" && "${2:-}" == "--json=short" && $# == 2 ]]; then
  printf '[{"hierarchy":"/usr","extensions":['
  separator=""
  while IFS= read -r name; do
    [[ "$name" == veyra-* ]] || continue
    printf '%s"%s"' "$separator" "$name"
    separator=,
  done < <(rpm --root /var/lib/veyra/vpkg/root -qa --qf '%{NAME}\n')
  printf '],"since":null}]\n'
  exit 0
elif [[ "${1:-}" != "refresh" || $# != 1 ]]; then
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

# Add a second, signed version to the testing repository to exercise a real
# DNF5 update transaction rather than only the already-up-to-date code path.
upgrade_build=/run/veyra-vpkg-ci/upgrade-rpmbuild
mkdir -p "$upgrade_build"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
cp "$extension_image" "$upgrade_build/SOURCES/veyra-shell-tools.raw"
cat > "$upgrade_build/SPECS/veyra-shell-tools.spec" <<'EOF'
Name:           veyra-shell-tools
Version:        0.2.0
Release:        1.fc44
Summary:        Veyra package transaction integration fixture
License:        GPL-2.0-or-later AND MIT AND Unlicense
BuildArch:      x86_64
Source0:        veyra-shell-tools.raw

%description
Veyra package transaction integration fixture.

%prep

%build

%install
install -D -m 0644 %{SOURCE0} %{buildroot}/var/lib/extensions/veyra-shell-tools.raw

%files
/var/lib/extensions/veyra-shell-tools.raw
EOF
rpmbuild -bb "$upgrade_build/SPECS/veyra-shell-tools.spec" \
  --define "_topdir $upgrade_build" \
  --define "_sourcedir $upgrade_build/SOURCES" \
  --define '_build_name_fmt %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}.rpm'
upgrade_rpm="$(find "$upgrade_build/RPMS" -type f -name '*.rpm' -print -quit)"
test -s "$upgrade_rpm"
fingerprint="$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')"
test -n "$fingerprint"
rpmsign --define "_gpg_name $fingerprint" --addsign "$upgrade_rpm"
testing_repo="$site/packages/testing/x86_64"
cp "$upgrade_rpm" "$testing_repo/"
createrepo_c --database --update "$testing_repo"
gpg --batch --yes --armor --local-user "$fingerprint" --detach-sign \
  --output "$testing_repo/repodata/repomd.xml.asc" \
  "$testing_repo/repodata/repomd.xml"
dnf_ci=(
  dnf5
  "--installroot=$install_root"
  --use-host-config
  --releasever=44
  --setopt=system_state_dir=/var/lib/veyra/vpkg/state
  --setopt=transaction_history_dir=/var/lib/veyra/vpkg/history
  --setopt=persistdir=/var/lib/veyra/vpkg/persist
  --setopt=system_cachedir=/var/cache/veyra/vpkg
  --setopt=keepcache=True
  --setopt=install_weak_deps=False
  --repo=veyra-testing
  --refresh
)
"${dnf_ci[@]}" clean metadata
available_versions="$("${dnf_ci[@]}" repoquery --available veyra-shell-tools)"
printf 'Available testing packages:\n%s\n' "$available_versions"
if ! grep -Fq '0.2.0' <<<"$available_versions"; then
  echo "The signed testing repository does not expose veyra-shell-tools 0.2.0." >&2
  exit 1
fi
upgrade_versions="$("${dnf_ci[@]}" repoquery --upgrades veyra-shell-tools)"
printf 'DNF5 upgrade candidates:\n%s\n' "$upgrade_versions"
if ! grep -Fq '0.2.0' <<<"$upgrade_versions"; then
  echo "DNF5 does not consider veyra-shell-tools 0.2.0 an upgrade candidate." >&2
  exit 1
fi
PATH="$shim:$PATH" "$vpkg" upgrade
installed_version="$(rpm --root "$install_root" -q --qf '%{VERSION}' veyra-shell-tools)"
if [[ "$installed_version" != "0.2.0" ]]; then
  echo "vpkg did not upgrade veyra-shell-tools to 0.2.0 (found $installed_version)." >&2
  exit 1
fi
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

echo "vpkg install, channel isolation, real upgrade, no-op upgrade, remove, and activation-failure rollback passed."
