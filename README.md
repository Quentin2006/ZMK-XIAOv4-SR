# Skree Control

A local web control panel for this **62-key Skree Std with a right PMW3610 trackball**. Select keys on a split layout, edit four layers, adjust trackball preferences, review the generated changes, and commit and push them to this repository using your existing GitHub sign-in.

## Run locally

Requires Python 3.12+, Git, and an authenticated GitHub CLI (`gh auth login`). Desktop apply also requires the installed Hyprland Lua configuration and `xkbcli`.

```sh
git switch skree-control-panel
python3 editor/install-local.py
```

Open **http://127.0.0.1:8766**. The installer adds a user service that starts at login and configures this checkout to use GitHub CLI's existing credential helper. It stores no token in the application or repository. Keep this checkout at its installed location; rerun the installer after moving it.

```sh
systemctl --user status skree-control
systemctl --user restart skree-control
journalctl --user -u skree-control -n 30
# To stop automatic startup:
systemctl --user disable --now skree-control
```

For a temporary foreground session, stop the service first and run `python3 editor/server.py`. The server always binds to `127.0.0.1`, with local host/origin and CSRF checks. This app is intended for your computer, not public hosting.

## Edit and save

1. Select a layer and a physical key. Choose a key, modifier expression such as `LC(C)`, layer action, mouse button, Bluetooth action, or lighting action. You can also record a single physical key.
2. Adjust Desktop and Trackball preferences as needed. Unsaved drafts stay in your browser; JSON export is available.
3. Choose **Review & save** to inspect the changed keys and exact file diffs. **Commit & push to GitHub** writes a commit to `skree-control-panel` in `Quentin2006/ZMK-XIAOv4-SR`.
4. For computer settings, open **Desktop → Apply saved desktop profile**. For firmware changes, build and flash separately.

History can load an earlier configuration as a draft; saving it creates a new commit. A failed push retains the local commit and offers Retry push, including after a page reload. Refresh from GitHub fetches this branch and permits fast-forward updates only. Other local edits, stale revisions, changed remotes, and generated-source drift block writes for review.

The editor intentionally uses a separate branch. Merge its pull request when ready; it does not automatically modify `main`.

## What takes effect where

| Setting | Takes effect |
| --- | --- |
| Slash/backslash swap, hold-button scrolling, middle-button emulation, scroll speed/direction, pointer sensitivity | On this computer after saving and applying the Desktop profile |
| Key bindings, layers, sensor CPI, automatic mouse-layer timeout | On the keyboard after building and flashing matching firmware |

The trackball now uses **3200 CPI**, a matching **3200-DPI desktop declaration**, and **flat acceleration**. Flat sensitivity `-0.4` selects a constant gain of 0.6 in the installed libinput 1.32.0; after DPI normalization that gives roughly 600 pointer units per inch of ball-surface movement. It is a starting point to tune by feel.

The panel's **Declared sensor CPI** is separate from the firmware setting: it must describe the firmware actually flashed, not a pending build. Historical configurations migrate to adaptive acceleration with no DPI override.

The generated, narrowly matched system declaration is stored in `desktop/linux/99-skree-control.hwdb`. After changing Declared sensor CPI and saving, install it locally:

```sh
python3 editor/install-dpi.py --dry-run
pkexec /usr/bin/python3 "$PWD/editor/install-dpi.py"
```

This backs up any previous managed declaration, rebuilds the system hardware database, and updates only Skree input nodes. Reconnect the keyboard afterwards so libinput reads the new resolution. The web server never invokes the privileged installer. Desktop Apply separately applies acceleration and sensitivity through the user-owned Hyprland module. Other mice do not match these DPI rules.

The baseline desktop profile retains the existing slash swap and right-button hold-to-scroll. Left+right mouse-button emulation is optional. **Right-click + Backspace is not implemented as a desktop chord.** To assign a dedicated middle-click key in firmware, choose a key on the Mouse layer and set Mouse button → Middle click. This changes that key whenever the Mouse layer is active.

Desktop apply backs up `input.lua`, the managed module, and the XKB map in ignored `.local-state/desktop-backups/`. It migrates the two prior Skree blocks to `require("hypr.skree-control")`, preserves other settings, compiles the XKB map, reloads Hyprland, and checks configuration errors. Failure restores the previous files and reloads them. No firmware is flashed by the site.

## Current keyboard profile

Use **Base**, one held **Fn** layer, and the automatic **Mouse** layer. The right thumb key previously labeled L2 now sends standard Alt (`&kp LALT`), matching the existing left-side Alt. Hold the left L1 thumb key for Fn.

| Hold Fn and press | Result |
| --- | --- |
| 1 through 0 | F1 through F10 |
| Minus / Equals | F11 / F12 |
| H / J / K / L | Left / Down / Up / Right |
| Physical Left / Right arrows | Home / End |
| Physical Up / Down arrows | Page Up / Page Down |
| N / M | Play or pause / Mute |
| Comma / Period | Volume down / Volume up |

Other Fn positions pass through to Base. Fn+Esc sends ordinary Escape. The automatic Mouse layer retains its vendor click assignments and 500 ms timeout. The unused logical Layer 2 slot retains the old system bindings for reference, but no physical key activates it. Keeping that slot preserves the established Mouse layer number 3.

These profile changes are saved firmware source. Build and flash both matching halves to activate them on the keyboard.

## Hardware and source of truth

The original repository configured a different, 60-key shift-register board. This branch imports the correct vendor profile from [WainingForests/ZMK-XIAOv4-Std](https://github.com/WainingForests/ZMK-XIAOv4-Std/tree/NS-5x6%2B6%2B4-%2862%29%2BTBx1), branch `NS-5x6+6+4-(62)+TBx1`, commit `487828c994aea35e24bdb38051a0ac7a4ef64c04`. Both physical halves were previously flashed and read back against that vendor build. The current profile customizes its Fn layer and right Alt thumb key as documented above.

`editor/config.json` records the configuration. The generator updates only:

- the four 62-position binding arrays in `config/skreecustom.keymap`;
- the automatic mouse-layer timeout in `boards/shields/skreecustom/skreecustom.dtsi`;
- the sensor CPI in `boards/shields/skreecustom/skreecustom_right.overlay`;
- `desktop/hyprland/skree-control.lua`.
- `desktop/linux/99-skree-control.hwdb` (install separately after changing the declared CPI).

The full slash-swap XKB map is stored in `desktop/hyprland/skree-swapped-slashes.xkb`. Matrix IDs in `editor/static/geometry.json` follow the electrical transform, including the asymmetric 32-key left / 30-key right halves. Key positions are schematic. The known non-responsive slash position remains a separate hardware/mapping investigation; this editor does not repair an electrical fault.

For manual JSON edits, run `python3 editor/configure.py` and commit the generated files together. Manual source edits must be reconciled into the JSON before the panel can save; they are never silently overwritten. The editor supports the current four-layer profile and a validated set of ZMK behaviors rather than arbitrary device-tree source.

## Firmware builds

Pushing runs the repository's ZMK v0.3 workflow for `seeeduino_xiao_ble` with `skreecustom_left` and `skreecustom_right`. Download the successful run's firmware artifact. Verify the run belongs to your intended commit before flashing.

- **Physical left, without trackball:** flash `skreecustom_left`.
- **Physical right, blue trackball:** flash `skreecustom_right`.

Use a USB data cable and double-press the half's physical reset button to enter its UF2 bootloader, then copy only the matching image to that half's drive. Keep settings-reset images separate. Flashing is a manual step outside this app.

## Validate

```sh
python3 editor/configure.py --check
python3 -m unittest discover -s editor -p 'test_*.py' -v
node --check editor/static/app.js
```

Tests cover all 248 bindings, matrix identities, validation, exact edits, real commits and pushes to a disposable local bare repository, stale/dirty/source-drift rejection, push recovery, historical restore, desktop migration and rollback, and local HTTP protections. CI runs the same checks alongside the firmware builds. No JavaScript package installation or frontend build is required.
