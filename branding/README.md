# Veyra branding

The canonical Veyra mark and accompanying Fastfetch/boot-menu artwork were carried forward from the historical [`angyedz/Veyra-Linux`](https://github.com/angyedz/Veyra-Linux) repository at commit [`873a4ab7eb8c080e0d8ab94d15ecea9635e072c0`](https://github.com/angyedz/Veyra-Linux/tree/873a4ab7eb8c080e0d8ab94d15ecea9635e072c0/branding). The SVG is preserved byte-for-byte.

The image currently installs the SVG and Fastfetch artwork under `/usr/share/veyra/`. `grub/theme.txt` is retained as a visual reference only: the current image boots with systemd-boot. The old `os-release` file is deliberately not copied because it identifies the historical Alpine/musl system; the new image is Fedora-based.

## KDE Plasma colors

The image ships `Veyra Dark` at `/usr/share/color-schemes/VeyraDark.colors` and selects it as the system default for Plasma and KDE applications. The default is not locked: a user's color choice in System Settings takes precedence. The palette reuses the logo and boot-menu colors: sky blue `#55B8FF`, frost white `#EAF8FF`, muted blue `#A5BBD0`, and deep navy `#0F1B2A`. Normal and selected text combinations are checked for WCAG AA contrast.

KDE documents system color schemes under `/usr/share/color-schemes/` and cascading system defaults that users can override in their own configuration: [Plasma themes and plugins](https://develop.kde.org/docs/plasma/), [KDE configuration hierarchy](https://develop.kde.org/docs/administration/kiosk/introduction/).
