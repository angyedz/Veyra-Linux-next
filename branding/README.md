# Veyra branding

The canonical Veyra mark and accompanying Fastfetch/boot-menu artwork were carried forward from the historical [`angyedz/Veyra-Linux`](https://github.com/angyedz/Veyra-Linux) repository at commit [`873a4ab7eb8c080e0d8ab94d15ecea9635e072c0`](https://github.com/angyedz/Veyra-Linux/tree/873a4ab7eb8c080e0d8ab94d15ecea9635e072c0/branding). The SVG is preserved byte-for-byte.

The SVG is converted by `scripts/generate-fetch-logos.py` into one shared ASCII silhouette and small color-tagged adapters for Fastfetch, Neofetch, and Screenfetch. The image installs the shared logo under `/usr/share/veyra/branding/` and the adapters under `/usr/share/veyra/fastfetch/`, `/usr/share/veyra/neofetch/`, and `/usr/share/veyra/screenfetch/`.

Fastfetch uses the system XDG config, which changes only its logo and keeps the built-in information and layout. New user profiles receive Neofetch and HyFetch configs that only select the Veyra logo; HyFetch uses Fastfetch as its backend. Bash sessions automatically give Screenfetch the same mark when that optional program is installed; its normal information and layout stay intact. Other fetch programs can use `/usr/share/veyra/branding/fetch-logo.txt` as a plain ASCII source. `/etc/os-release` identifies the image as Veyra Next while retaining Fedora 44 as the base release and `ID_LIKE=fedora` for compatibility.

`grub/theme.txt` is retained as a visual reference only: the current image boots with systemd-boot.
