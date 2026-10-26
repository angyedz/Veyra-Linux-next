#!/usr/bin/env python3
"""Render Veyra's SVG mark into compatible terminal fetch logos.

Requires ImageMagick with SVG support. The generated files are committed so
building the operating system does not depend on ImageMagick.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "branding/veyra-logo.svg"
WIDTH = 38
HEIGHT = 14


def render_pixels() -> list[list[tuple[str, str | None]]]:
    result = subprocess.run(
        [
            "magick",
            "-background",
            "none",
            "-density",
            "512",
            str(SVG),
            "-trim",
            "+repage",
            "-resize",
            f"{WIDTH}x{HEIGHT}!",
            "-depth",
            "8",
            "rgba:-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    )
    expected_size = WIDTH * HEIGHT * 4
    if len(result.stdout) != expected_size:
        raise RuntimeError(
            f"ImageMagick returned {len(result.stdout)} bytes; expected {expected_size}"
        )

    pixels: list[list[tuple[str, str | None]]] = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            red, green, _blue, alpha = result.stdout[(y * WIDTH + x) * 4 : (y * WIDTH + x + 1) * 4]
            if alpha < 100:
                row.append((" ", None))
            elif red > 200 and green > 220:
                row.append(("+", "2"))
            else:
                row.append(("#", "1"))
        pixels.append(row)
    return pixels


def render_logo(pixels: list[list[tuple[str, str | None]]], style: str) -> str:
    markers = {"fastfetch": {"1": "$1", "2": "$2"}, "neofetch": {"1": "${c1}", "2": "${c2}"}}
    if style == "plain":
        markers_for_style: dict[str, str] = {}
    else:
        markers_for_style = markers[style]

    lines = []
    for row in pixels:
        line = []
        previous_color = None
        for char, color in row:
            if color is None:
                line.append(" ")
                continue
            if color != previous_color:
                line.append(markers_for_style.get(color, ""))
                previous_color = color
            line.append(char)
        lines.append("".join(line).rstrip())
    return "\n".join(lines).rstrip() + "\n"


def render_screenfetch_logo(pixels: list[list[tuple[str, str | None]]]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# Generated from branding/veyra-logo.svg by scripts/generate-fetch-logos.py.",
        'if [[ "$no_color" != "1" ]]; then',
        "    c1=$(getColor 'light cyan')",
        "    c2=$(getColor 'white')",
        "else",
        '    c1=""',
        '    c2=""',
        "fi",
        'if [ -n "${my_lcolor}" ]; then c1="${my_lcolor}"; c2="${my_lcolor}"; fi',
        'startline="0"',
        f'logowidth="{WIDTH}"',
        "fulloutput=(",
    ]
    for row in pixels:
        line = []
        previous_color = None
        for char, color in row:
            if color is None:
                line.append(" ")
                continue
            if color != previous_color:
                line.append("${c1}" if color == "1" else "${c2}")
                previous_color = color
            line.append(char)
        lines.append(f'"{"".join(line).rstrip()}%s"')
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_matching(source: str, *paths: str) -> None:
    content = source.encode("utf-8")
    for relative in paths:
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def main() -> None:
    pixels = render_pixels()
    write_matching(
        render_logo(pixels, "plain"),
        "branding/fetch/logo.txt",
        "mkosi.extra/usr/share/veyra/branding/fetch-logo.txt",
    )
    write_matching(
        render_logo(pixels, "fastfetch"),
        "branding/fastfetch/logo.txt",
        "mkosi.extra/usr/share/veyra/fastfetch/logo.txt",
    )
    write_matching(
        render_logo(pixels, "neofetch"),
        "branding/neofetch/logo.txt",
        "mkosi.extra/usr/share/veyra/neofetch/logo.txt",
    )
    write_matching(
        render_screenfetch_logo(pixels),
        "branding/screenfetch/logo.sh",
        "mkosi.extra/usr/share/veyra/screenfetch/logo.sh",
    )


if __name__ == "__main__":
    main()
