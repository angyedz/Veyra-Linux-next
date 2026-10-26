# Veyra Glacier — Plasma 6 visual system (v0.3)

Veyra Next ships a two-mode original desktop theme inspired by the **design
principles** of Fluent: quiet visual hierarchy, modest rounded geometry,
careful contrast, and consistent application surfaces. It is not a Windows
or macOS clone. It does not include macOS traffic-light window controls.

## Included components

- `VeyraGlacier.colors` and `VeyraGlacierLight.colors`: tested KDE
  color schemes (normal text/background contrast >= 4.5:1).
- `/usr/share/plasma/desktoptheme/veyra-glacier-{dark,light}`: two
  native Plasma styles with 9-slice vector surfaces for floating panels,
  popups, desktop widgets, tooltips, task buttons, and selection states.
  Missing components use the upstream Breeze fallback.
- `/usr/share/plasma/look-and-feel/org.veyra.glacier.{dark,light}`: two
  independently selectable KDE Global Themes bundling Plasma style,
  icon style, color scheme and right-aligned native window controls.
- `/usr/share/wallpapers/VeyraGlacier/`: original lightweight vector
  wallpaper in both light and dark variants using Plasma's day/night
  wallpaper package convention (`images/` and `images_dark/`).
- `/usr/bin/veyra-theme`: unprivileged opt-in theme switcher.

## How to use

Log in to the graphical KDE Plasma session. Either use **System Settings
→ Colors & Themes → Global Theme → Veyra Glacier Dark/Light**, or run:

```sh
veyra-theme dark
veyra-theme light
veyra-theme status
```

The selector refuses root users and headless sessions and applies KDE
Global Theme packages through `plasma-apply-lookandfeel`. KDE's own
day/night switching can also use the separately installed light and dark
packages. This does **not** silently enable time-based automation.
Users can keep their preferred wallpaper and choose components individually.

The default distribution theme is Dark through `/etc/xdg/kdeglobals`
and `/etc/xdg/plasmarc`. User files under `~/.config` are never
overwritten by image construction. KDE may ask whether to apply its
global theme components; selection is up to the user.

## Invariants / ergonomics

- **Titlebars:** right-hand Minimize / Maximize / Close, `IAX`.
  No left-side traffic lights or titlebars stolen by maximized windows.
- **Controls:** Breeze QStyle preserves tested hit targets, keyboard
  navigation, accessibility, high-contrast alternatives, and app
  compatibility. We theme the Plasma shell separately via SVG assets.
- **Effects:** no custom QML animation loops, raster blur filters,
  compositor hacks, forced translucency, user session services, or
  permanently running helper daemons. No performance improvement is
  claimed without cold-boot and frametime measurements.
- **Wallpaper:** SVG graphics are static; Plasma's built-in image
  plugin handles rendering and may dynamically select the light/dark
  variant. Actual guest-visible image selection still needs QA.
- **Independence:** UEFI, BIOS/CSM, OTA and Recovery boot logic are
  outside the theme PR. Never modify the host bootloader.

## Verification

Source contract checks (including the KDE theme element/state reference): `python3 -m unittest discover -s tests -p
'test_glacier*.py' -v`. CI also builds the full Fedora 44 image and
checks on-image files and KDE Global Theme registration in mkosi shell.

**Visual QA still required before merging:** run a disposable Fedora 44
KDE Plasma Wayland VM. Confirm both global themes appear in System
Settings; switch dark → light → dark; check wallpaper changes, button
position, Plasma panels and popups, disabled/reduced-motion preference,
125%/200% scaling, and at least one non-Qt/GTK app. Check session logs
for SVG or KPackage warnings. No real screenshots or measured speedups
are claimed by these source tests.
