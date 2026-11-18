#!/usr/bin/env python3
"""Push a color to the Skree Custom keyboard's host-controlled RGB underglow.

The matching firmware publishes a second HID interface (usage page 0xFF60,
usage 0x61) over both USB and Bluetooth. BlueZ exposes that interface as a
hidraw node alongside the keyboard's normal one; this finds it by its report
descriptor rather than by number, so it survives reconnects.
"""

import argparse
import colorsys
import glob
import json
import os
import sys

MAGIC = b"SK"
CMD_SET_HSB = 0x01
CMD_SET_HS = 0x02
REPORT_SIZE = 32
# Usage page 0xFF60 (2-byte item), usage 0x61 - unique to the raw HID adapter.
USAGE_SIGNATURE = b"\x06\x60\xff\x09\x61"
SKREE_VID_PID = ("1D50", "615E")
STATE_FILE = os.path.expanduser("~/.local/state/skree-rgb.json")


def sysfs(path):
    return "/sys/class/hidraw/" + os.path.basename(path) + "/device"


def raw_hid_devices():
    for path in sorted(glob.glob("/dev/hidraw*")):
        try:
            with open(os.path.join(sysfs(path), "uevent")) as handle:
                uevent = handle.read().upper()
        except OSError:
            continue
        if not all(token in uevent for token in SKREE_VID_PID):
            continue
        try:
            with open(os.path.join(sysfs(path), "report_descriptor"), "rb") as handle:
                descriptor = handle.read()
        except OSError:
            continue
        if USAGE_SIGNATURE in descriptor:
            yield path


def hex_to_hsb(value, brightness):
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError("expected a 6-digit hex color")
    red, green, blue = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, saturation, value_brightness = colorsys.rgb_to_hsv(red, green, blue)
    return (
        round(hue * 360) % 360,
        round(saturation * 100),
        round(value_brightness * brightness),
    )


def build_report(value, brightness):
    hue, saturation, level = hex_to_hsb(value, brightness)
    if not 0 <= hue <= 360 or not 0 <= saturation <= 100 or not 0 <= level <= 100:
        raise ValueError("computed color is out of range")
    report = bytearray(REPORT_SIZE)
    report[0:2] = MAGIC
    report[2] = CMD_SET_HSB
    report[3] = hue >> 8
    report[4] = hue & 0xFF
    report[5] = saturation
    report[6] = level
    return bytes(report), (hue, saturation, level)


def report_from_hsb(hue, saturation, brightness=None):
    if not 0 <= hue <= 360 or not 0 <= saturation <= 100:
        raise ValueError("color is out of range")
    report = bytearray(REPORT_SIZE)
    report[0:2] = MAGIC
    if brightness is None:
        report[2] = CMD_SET_HS
    else:
        if not 0 <= brightness <= 100:
            raise ValueError("brightness is out of range")
        report[2] = CMD_SET_HSB
        report[6] = brightness
    report[3] = hue >> 8
    report[4] = hue & 0xFF
    report[5] = saturation
    return bytes(report)


def send_report(report):
    devices = list(raw_hid_devices())
    if not devices:
        print("Skree raw HID interface not found (is it connected and paired?)", file=sys.stderr)
        return False
    for path in devices:
        try:
            with open(path, "wb", buffering=0) as handle:
                handle.write(report)
        except OSError as error:
            print(f"failed to write {path}: {error}", file=sys.stderr)
            continue
        return True
    return False


def save_state(hsb):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as handle:
        json.dump({"h": hsb[0], "s": hsb[1], "b": hsb[2]}, handle)


def load_state():
    try:
        with open(STATE_FILE) as handle:
            state = json.load(handle)
        hsb = (state["h"], state["s"], state["b"])
        if 0 <= hsb[0] <= 360 and 0 <= hsb[1] <= 100 and 0 <= hsb[2] <= 100:
            return hsb
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return (0, 0, 20)


def step_brightness(step=10):
    """Advance brightness by step percent, wrapping around at 100."""
    hue, saturation, brightness = load_state()
    brightness = (brightness + step) % 101
    if send_report(report_from_hsb(hue, saturation, brightness)):
        save_state((hue, saturation, brightness))
        print(f"brightness {brightness}% (HSB{(hue, saturation, brightness)})", flush=True)
        return brightness
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("color", nargs="?", help="color as #RRGGBB")
    parser.add_argument(
        "--brightness", type=int, default=35,
        help="cap brightness at this percent (default: 35, a dim backlight)",
    )
    parser.add_argument(
        "--step", type=int, metavar="N",
        help="instead of setting a color, advance brightness by N percent (wrap at 100)",
    )
    parser.add_argument(
        "--keep-brightness", action="store_true",
        help="apply only the color's hue/saturation; leave brightness on the keyboard alone",
    )
    args = parser.parse_args()

    if args.step is not None:
        result = step_brightness(args.step)
        return 0 if result is not None else 1

    if not args.color:
        parser.error("give a color or --step")
    if not 0 <= args.brightness <= 100:
        parser.error("--brightness must be between 0 and 100")

    hue, saturation, level = hex_to_hsb(args.color, 100)
    hsb = (hue, saturation, level)
    if args.keep_brightness:
        report = report_from_hsb(hue, saturation)
    else:
        report = report_from_hsb(hue, saturation, round(level * args.brightness / 100))

    if not send_report(report):
        return 1
    save_state(hsb)
    print(f"sent {args.color} as HSB{hsb}" + (" (brightness kept)" if args.keep_brightness else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
