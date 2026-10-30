Name:           veyra-shell-tools
Version:        0.1.0
Release:        1%{?dist}
Summary:        Extra interactive shell tools for Veyra Linux
License:        GPL-2.0-or-later AND MIT AND Unlicense
BuildArch:      x86_64
Source0:        veyra-shell-tools.raw

%description
An installable Veyra system extension containing optional command-line tools.
The RPM payload is only the extension image under /var/lib/extensions within
vpkg's separate installroot. The image is linked from /etc/extensions and
supplies files under /usr without changing the immutable OS or its RPM database.

%prep

%build

%install
install -D -m 0644 %{SOURCE0} %{buildroot}/var/lib/extensions/veyra-shell-tools.raw

%files
/var/lib/extensions/veyra-shell-tools.raw

