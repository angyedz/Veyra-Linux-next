# Veyra Glacier — design system v0.2

A native KDE Plasma 6 visual language inspired by the **principles** of
Fluent and modern macOS: hierarchy, restraint, readable surfaces and depth.
Not a Windows clone, not macOS traffic lights, and not a translucent-everywhere
theme.

## Two modes

- `VeyraGlacier.colors`: midnight navy surfaces, cool ice-blue accents.
- `VeyraGlacierLight.colors`: pearl-white surfaces, slate text and blue accents.

Both ship under `/usr/share/color-schemes`. The distribution default is
dark, but users can switch in **System Settings → Colors** without a
reboot. User overrides in `~/.config` must remain intact.

## Visual contract

- **Window buttons:** right aligned, conventional minimize / maximize /
  close (`IAX`); never macOS red/yellow/green traffic-light controls.
- **Spacing:** use KDE/Qt's native scaling and accessible hit targets;
  avoid custom per-app overrides and tiny controls.
- **Geometry:** modest corner rounding, not oversized pill shapes.
- **Materials:** mostly opaque surfaces; reserve blur/translucency for
  temporary menus and overlays after GPU cost is measured.
- **Motion:** brief functional transitions only, respecting reduced-motion
  preferences. Do not enable expensive effects just for screenshots.
- **Typography:** native system font stack; no redistribution of
  proprietary Apple/Microsoft fonts.
- **Accessibility:** preserve keyboard focus, selection contrast and
  KDE's high-contrast options.

## Scope and next phases

Current implementation: two KDE/Qt color schemes, Breeze-based defaults,
right-side window-control layout and source validation. This is **not yet**
a complete custom Plasma SVG style, KWin decoration or Global Theme.
Those should be implemented and tested in separate changes, including
actual light/dark screenshots in a disposable Plasma Wayland session.

No modifications to an existing user's home directory. No system services
are disabled as a supposed performance optimization.
