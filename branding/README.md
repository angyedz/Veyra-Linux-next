# Veyra branding

The canonical Veyra mark and accompanying Fastfetch/boot-menu artwork were carried forward from the historical [`angyedz/Veyra-Linux`](https://github.com/angyedz/Veyra-Linux) repository at commit [`873a4ab7eb8c080e0d8ab94d15ecea9635e072c0`](https://github.com/angyedz/Veyra-Linux/tree/873a4ab7eb8c080e0d8ab94d15ecea9635e072c0/branding). The SVG is preserved byte-for-byte.

The image currently installs the SVG and Fastfetch artwork under `/usr/share/veyra/`. `grub/theme.txt` is retained as a visual reference only: the current image boots with systemd-boot. The old `os-release` file is deliberately not copied because it identifies the historical Alpine/musl system; the new image is Fedora-based.

## Default Fastfetch + Veyra Glacier themes (Fedora 44)

`fastfetch` is installed in both the immutable installed-system image (mkosi)
and the Lorax-based Live Installer ISO (Kickstart). Its existing Veyra
ASCII logo and branded `config.jsonc` are exposed through the default
system-wide XDG lookup path `/etc/xdg/fastfetch/config.jsonc`. The OS
label reports "Veyra Next (Fedora 44)" rather than disguising the Fedora
base or changing the low-level `ID` needed by systemd/rpm. User
`~/.config/fastfetch/config.jsonc` takes precedence; `fastfetch -c none`
still gives upstream defaults.

The Veyra Glacier dark/light KDE asset trees, color schemes, and the
`veyra-theme {dark|light|status}` helper are carried from
`feature/veyra-fluent-dual-theme` without replacing the A/B boot,
dm-verity, or installer implementation. XDG global defaults are used
instead of modifying existing user homes. Run `veyra-theme dark` or
`veyra-theme light` inside an unprivileged Plasma session to switch.
The installed image and the legacy Live ISO ship the same assets; a
separate native-Live experiment branch is not changed by this commit.
