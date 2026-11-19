#!/usr/bin/env python3
"""Push a color to the Skree Custom keyboard's host-controlled RGB underglow.

Over USB the color goes out the raw HID hidraw node (found by its report
descriptor rather than by number, so it survives reconnects). Over Bluetooth,
BlueZ claims every HID service, so this script falls back to writing the
custom GATT characteristic c0de5001-5c3e-4b8a-9d4a-736b72656500 via busctl.
"""

import argparse
import colorsys
import glob
import json
import os
import re
import subprocess
import sys
import tomllib

MAGIC = b"SK"
CMD_SET_HSB = 0x01
CMD_SET_HS = 0x02
REPORT_SIZE = 32
# Usage page 0xFF60 (2-byte item), usage 0x61 - unique to the raw HID adapter.
USAGE_SIGNATURE = b"\x06\x60\xff\x09\x61"
SKREE_VID_PID = ("1D50", "615E")
STATE_FILE = os.path.expanduser("~/.local/state/skree-rgb.json")
# Custom (non-HID) GATT characteristic the firmware publishes over Bluetooth.
BLE_CHAR_UUID = "c0de5001-5c3e-4b8a-9d4a-736b72656500"

# Saturation floor below which --vivid treats an accent as grayish, and the
# saturation a vivid color is punched up to.
GRAYISH_SATURATION = 25
VIVID_SATURATION = 75

# Color entries of an Omarchy colors.toml, in no particular order: the indexed
# colorN form, plus the named keys Omarchy themes actually use.
PALETTE_COLOR_KEYS = [f"color{i}" for i in range(16)] + [
    "red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown",
    "bright_red", "bright_yellow", "bright_green", "bright_cyan",
    "bright_blue", "bright_magenta",
]


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


def most_saturated_palette_color(path):
    """(hue, saturation) of the most saturated color in a palette, or None.

    Reads the colorN and named color entries of an Omarchy colors.toml; a
    missing or unparsable palette simply yields None.
    """
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return None
    best = None
    for key in PALETTE_COLOR_KEYS:
        value = data.get(key)
        if not isinstance(value, str):
            continue
        try:
            hue, saturation, _ = hex_to_hsb(value, 100)
        except ValueError:
            continue
        if best is None or saturation > best[1]:
            best = (hue, saturation)
    return best


def vivid_hsb(hue, saturation, palette_path):
    """Vivid variant of a color: same hue with the saturation punched up.

    Grayish accents (saturation below 25) borrow the hue of the most
    saturated palette color instead, or fall back to soft white when no
    palette color is saturated enough.
    """
    if saturation >= GRAYISH_SATURATION:
        return hue, VIVID_SATURATION
    pick = most_saturated_palette_color(palette_path)
    if pick is not None and pick[1] >= GRAYISH_SATURATION:
        return pick[0], VIVID_SATURATION
    return hue, 0


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


def send_hidraw(report, quiet=False):
    devices = list(raw_hid_devices())
    if not devices:
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


def ble_characteristics():
    try:
        result = subprocess.run(
            ["busctl", "--json=short", "call", "org.bluez", "/",
             "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    try:
        entries = json.loads(result.stdout)["data"]
    except (ValueError, KeyError, TypeError):
        return []

    objects = {}
    for entry in entries:
        if isinstance(entry, dict):
            objects.update(entry)

    for path, interfaces in objects.items():
        char = interfaces.get("org.bluez.GattCharacteristic1")
        if char is None:
            continue
        if char.get("UUID", {}).get("data", "").lower() != BLE_CHAR_UUID:
            continue
        match = re.match(r"^(/org/bluez/hci\d+/dev_[0-9A-F_]+)", path)
        if not match:
            continue
        device = match.group(1)
        if objects.get(device, {}).get("org.bluez.Device1", {}).get(
            "Connected", {}
        ).get("data") is True:
            yield path


def send_ble(report):
    payload = bytes(report)[:7]
    for path in ble_characteristics():
        command = [
            "busctl", "call", "org.bluez", path,
            "org.bluez.GattCharacteristic1", "WriteValue", "aya{sv}",
            str(len(payload)),
        ]
        command.extend(str(byte) for byte in payload)
        command.extend(["1", "type", "s", "request"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
        print(result.stderr, file=sys.stderr, end="")
    return False


def send_report(report):
    if send_hidraw(report):
        return True
    if send_ble(report):
        return True
    print(
        "Skree keyboard not reachable over USB raw HID or Bluetooth (is it connected?)",
        file=sys.stderr,
    )
    return False


def save_state(hsb):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as handle:
        json.dump({"h": hsb[0], "s": hsb[1], "b": hsb[2]}, handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("color", nargs="?", help="color as #RRGGBB")
    parser.add_argument(
        "--brightness", type=int, default=35,
        help="cap brightness at this percent (default: 35, a dim backlight)",
    )
    parser.add_argument(
        "--keep-brightness", action="store_true",
        help="apply only the color's hue/saturation; leave brightness on the keyboard alone",
    )
    parser.add_argument(
        "--vivid", action="store_true",
        help="punch saturation up to 75; grayish accents borrow the hue of the "
             "most saturated color in --palette (soft white if none applies)",
    )
    parser.add_argument(
        "--palette", metavar="PATH",
        help="Omarchy colors.toml consulted by --vivid for grayish accents",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print what would be sent instead of sending it (state file untouched)",
    )
    args = parser.parse_args()

    if not args.color:
        parser.error("give a color")
    if not 0 <= args.brightness <= 100:
        parser.error("--brightness must be between 0 and 100")

    hue, saturation, level = hex_to_hsb(args.color, 100)
    if args.vivid:
        hue, saturation = vivid_hsb(hue, saturation, args.palette)
    hsb = (hue, saturation, level)
    if args.keep_brightness:
        report = report_from_hsb(hue, saturation)
    else:
        report = report_from_hsb(hue, saturation, round(level * args.brightness / 100))

    if args.dry_run:
        if args.keep_brightness:
            print(f"dry-run: would send hue {hue}, saturation {saturation}, brightness kept")
        else:
            print(f"dry-run: would send hue {hue}, saturation {saturation}, "
                  f"brightness {round(level * args.brightness / 100)}")
        return 0

    if not send_report(report):
        return 1
    save_state(hsb)
    print(f"sent {args.color} as HSB{hsb}" + (" (brightness kept)" if args.keep_brightness else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
