# Veyra branding

The canonical Veyra mark and accompanying Fastfetch/boot-menu artwork were carried forward from the historical [`angyedz/Veyra-Linux`](https://github.com/angyedz/Veyra-Linux) repository at commit [`873a4ab7eb8c080e0d8ab94d15ecea9635e072c0`](https://github.com/angyedz/Veyra-Linux/tree/873a4ab7eb8c080e0d8ab94d15ecea9635e072c0/branding). The SVG is preserved byte-for-byte.

The image currently installs the SVG and Fastfetch artwork under `/usr/share/veyra/`. `grub/theme.txt` is retained as a visual reference only: the current image boots with systemd-boot. The old `os-release` file is deliberately not copied because it identifies the historical Alpine/musl system; the new image is Fedora-based.
