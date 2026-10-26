#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
target_release="$(awk '
  /^\[Distribution\]$/ { section=1; next }
  /^\[/ { section=0 }
  section && /^Release=/ { sub(/^Release=/, ""); print; exit }
' "$root/mkosi.conf")"
[[ "$target_release" =~ ^[0-9]+$ ]] || {
  echo "mkosi.conf must declare a numeric Fedora target release." >&2
  exit 1
}
target_distribution="$(awk '
  /^\[Distribution\]$/ { section=1; next }
  /^\[/ { section=0 }
  section && /^Distribution=/ { sub(/^Distribution=/, ""); print; exit }
' "$root/mkosi.conf")"
[[ "$target_distribution" == fedora ]] || {
  echo "mkosi.conf target Distribution must be fedora (found: $target_distribution)." >&2
  exit 1
}
tools_tree_target="$(awk '
  /^\[Build\]$/ { section=1; next }
  /^\[/ { section=0 }
  section && /^(ToolsTree|ToolsTreeDistribution|ToolsTreeRelease)=/ { print }
' "$root/mkosi.conf")"
grep -Fxq 'ToolsTree=yes' <<<"$tools_tree_target" &&
grep -Fxq 'ToolsTreeDistribution=fedora' <<<"$tools_tree_target" &&
grep -Fxq "ToolsTreeRelease=$target_release" <<<"$tools_tree_target" || {
  echo "The mkosi tools tree must be enabled and pinned to Fedora $target_release." >&2
  exit 1
}
if [[ "${VEYRA_MKOSI_RUNNER:-}" == github-ubuntu-24.04 ]]; then
  [[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_OS:-}" == Linux ]] || {
    echo "The Ubuntu build path is restricted to the ephemeral GitHub Actions Linux runner." >&2
    exit 1
  }
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || {
    echo "The build runner must be Ubuntu 24.04." >&2
    exit 1
  }
  ! command -v dnf5 >/dev/null 2>&1 || {
    echo "The Ubuntu build runner must not use a host DNF installation." >&2
    exit 1
  }
  if [[ -d /etc/yum.repos.d ]] && find /etc/yum.repos.d -maxdepth 1 -type f -name '*.repo' -print -quit | grep -q .; then
    echo "The Ubuntu build runner must not configure host DNF repositories." >&2
    exit 1
  fi
  echo "Ephemeral Ubuntu 24.04 runner and Fedora $target_release mkosi tools tree are valid."
  exit 0
fi
if [[ ! -e /.dockerenv && ! -e /run/.containerenv ]]; then
  echo "Image composition must run inside an isolated Fedora container." >&2
  exit 1
fi
grep -Fxq 'ID=fedora' /etc/os-release || {
  echo "The build container must be Fedora $target_release." >&2
  exit 1
}
grep -Fxq "VERSION_ID=$target_release" /etc/os-release || {
  echo "The build container must be Fedora $target_release." >&2
  exit 1
}

awk -v release="$target_release" '
  function finish() {
    if (!repo) return
    if (enabled != "1") return
    if (repo == "fedora") expected="https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch"
    else if (repo == "updates") expected="https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever&arch=$basearch"
    else if (repo == "fedora-cisco-openh264") expected="https://mirrors.fedoraproject.org/metalink?repo=fedora-cisco-openh264-$releasever&arch=$basearch"
    else { print "Refusing non-Fedora enabled repo: " repo > "/dev/stderr"; failed=1; return }
    if (url_count != 1 || metalink != expected) {
      print "Refusing non-Fedora " release " URL for repo " repo ": " metalink > "/dev/stderr"
      failed=1
    }
    seen[repo]++
  }
  /^\[[^]]+\]$/ { finish(); repo=substr($0,2,length($0)-2); enabled="1"; url_count=0; metalink=""; next }
  /^[[:space:]]*enabled[[:space:]]*=/ { value=$0; sub(/^[^=]*=/,"",value); gsub(/[[:space:]"]/ ,"",value); enabled=value; next }
  /^[[:space:]]*(metalink|mirrorlist|baseurl)[[:space:]]*=/ {
    value=$0; sub(/^[^=]*=/,"",value); gsub(/^[[:space:]]+|[[:space:]]+$/,"",value)
    url_count++
    if ($0 ~ /^[[:space:]]*metalink[[:space:]]*=/) metalink=value
    next
  }
  END {
    finish()
    for (repo in seen) if (seen[repo] != 1) { print "Duplicate enabled Fedora repo: " repo > "/dev/stderr"; failed=1 }
    if (seen["fedora"] != 1 || seen["updates"] != 1 || seen["fedora-cisco-openh264"] != 1) {
      print "Fedora target repos must be enabled exactly once: fedora, updates, fedora-cisco-openh264" > "/dev/stderr"
      failed=1
    }
    exit failed
  }
' /etc/yum.repos.d/*.repo

enabled_repos="$(dnf5 repo list --enabled --quiet | awk 'NR > 1 { print $1 }' | LC_ALL=C sort)"
expected_repos="$(printf '%s\n' fedora updates fedora-cisco-openh264 | LC_ALL=C sort)"
[[ "$enabled_repos" == "$expected_repos" ]] || {
  echo "Enabled DNF repos must be only the configured Fedora $target_release repos; found: $enabled_repos" >&2
  exit 1
}

awk -v home="${HOME:-/root}" '
  function overlap(a,b) { return a == b || index(a,b "/") == 1 || index(b,a "/") == 1 }
  {
    mountpoint=$5; gsub(/\\040/," ",mountpoint)
    if (mountpoint == "/") next
    if (ENVIRON["GITHUB_ACTIONS"] == "true" && mountpoint == "/github/home") next
    if (mountpoint ~ /(^|\/)(\.cache|cache|dnf|libdnf5|mkosi)(\/|$)/ ||
        overlap(mountpoint,"/etc/dnf") || overlap(mountpoint,"/etc/yum.repos.d") ||
        overlap(mountpoint,"/var/cache/dnf") || overlap(mountpoint,"/var/cache/libdnf5") ||
        overlap(mountpoint,"/var/lib/dnf") || overlap(mountpoint,"/var/lib/libdnf5") ||
        overlap(mountpoint,home "/.cache")) {
      print "Refusing host DNF/cache mount at " mountpoint > "/dev/stderr"
      failed=1
    }
  }
  END { exit failed }
' /proc/self/mountinfo

echo "Isolated Fedora $target_release container and official Fedora repos are valid."
