#!/usr/bin/env python3
"""Shift+Tab on the Skree steps its RGB brightness (10% per press, wraps at 100).

Listens to the keyboard's evdev nodes; no firmware change or reflash needed.
Works whenever the raw HID channel is present (USB connection).
"""

import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evdev
from evdev import ecodes

import skree_rgb

SHIFT_KEYS = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
STEP = int(os.environ.get("SKREE_BRIGHTNESS_STEP", "10"))


def keyboards():
    found = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        name = device.name.lower()
        if "skree" in name and "keyboard" in name:
            found.append(device)
        else:
            device.close()
    return found


def main():
    devices = keyboards()
    if not devices:
        print("No Skree keyboard input device found", file=sys.stderr)
        return 1
    print("Watching:", ", ".join(d.name for d in devices), flush=True)

    shift_down = False
    last_refresh = time.monotonic()

    while True:
        try:
            if time.monotonic() - last_refresh > 10:
                devices = keyboards() or devices
                last_refresh = time.monotonic()
            readable, _, _ = select.select([d.fd for d in devices], [], [], 5)
            for fd in readable:
                device = next((d for d in devices if d.fd == fd), None)
                if device is None:
                    continue
                for event in device.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    if event.code in SHIFT_KEYS:
                        shift_down = event.value == 1
                    elif event.code == ecodes.KEY_TAB and event.value == 1 and shift_down:
                        skree_rgb.step_brightness(STEP)
        except OSError:
            time.sleep(2)
            devices = keyboards() or devices


if __name__ == "__main__":
    raise SystemExit(main())
