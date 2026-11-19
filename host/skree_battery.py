#!/usr/bin/env python3
"""Read both Skree Custom halves' battery levels from BlueZ into a state file.

Runs as a oneshot (driven by skree-battery.timer). It reads:

  * left  (the central half) = org.bluez.Battery1.Percentage on the Skree
    device, and
  * right (the peripheral half) = the second Battery Service's Battery Level
    characteristic (0x2a19), which the left half only exports after it is
    flashed with CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING/PROXY.

The two 0x180f Battery Services are told apart by reading BOTH 0x2a19
characteristics and matching one against Battery1.Percentage: the match is the
left/central half, the other is the right half. Before the new firmware is
flashed only one 0x180f service exists, so right is null. If both
characteristics read the same value, the lower handle is assumed to be left
(the central registers its own service first; the proxy service comes after).

Writes ${XDG_STATE_HOME:-~/.local/state}/skree-battery.json atomically and
exits 0 even when the keyboard is absent or disconnected.
"""

import json
import os
import subprocess
import time

DEVICE_NAME = "Skree Custom"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
TIMEOUT = 5

STATE_DIR = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
STATE_FILE = os.path.join(STATE_DIR, "skree-battery.json")


def get_managed_objects():
    """Return BlueZ's object map as {path: {interface: properties}}."""
    try:
        result = subprocess.run(
            [
                "busctl", "--json=short", "call", "org.bluez", "/",
                "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            ],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        entries = json.loads(result.stdout)["data"]
    except (ValueError, KeyError, TypeError):
        return {}
    objects = {}
    for entry in entries:
        if isinstance(entry, dict):
            objects.update(entry)
    return objects


def find_connected_device(objects):
    """Return (path, interfaces) for the connected Skree device, else None."""
    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if device is None:
            continue
        name = device.get("Alias", {}).get("data") or device.get("Name", {}).get("data")
        if name != DEVICE_NAME:
            continue
        if device.get("Connected", {}).get("data") is True:
            return path, interfaces
    return None


def battery_percentage(device_interfaces):
    """Return Battery1.Percentage on the device, or None if absent/invalid."""
    battery = device_interfaces.get("org.bluez.Battery1")
    if battery is None:
        return None
    percentage = battery.get("Percentage", {}).get("data")
    if isinstance(percentage, int) and 0 <= percentage <= 100:
        return percentage
    return None


def battery_level_characters(objects, device_path):
    """Yield (service_handle, char_path) for each 0x2a19 under a 0x180f service."""
    prefix = device_path + "/"
    services = []
    for path, interfaces in objects.items():
        if not path.startswith(prefix):
            continue
        service = interfaces.get("org.bluez.GattService1")
        if service is None:
            continue
        if service.get("UUID", {}).get("data", "").lower() != BATTERY_SERVICE_UUID:
            continue
        handle = service.get("Handle", {}).get("data")
        services.append((handle, path))
    services.sort(key=lambda entry: (entry[0] is None, entry[0] or 0))

    for handle, service_path in services:
        for path, interfaces in objects.items():
            char = interfaces.get("org.bluez.GattCharacteristic1")
            if char is None:
                continue
            if char.get("Service", {}).get("data") != service_path:
                continue
            if char.get("UUID", {}).get("data", "").lower() != BATTERY_LEVEL_CHAR_UUID:
                continue
            yield handle, path
            break


def read_level(char_path):
    """Read a Battery Level characteristic; return 0-100 or None."""
    try:
        result = subprocess.run(
            [
                "busctl", "call", "org.bluez", char_path,
                "org.bluez.GattCharacteristic1", "ReadValue", "a{sv}", "0",
            ],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # Successful output is "ay <n> <byte>..." (e.g. "ay 1 35").
    parts = result.stdout.split()
    if len(parts) < 3 or parts[0] != "ay":
        return None
    try:
        value = int(parts[2])
    except (ValueError, IndexError):
        return None
    return value if 0 <= value <= 100 else None


def write_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(state, handle)
    os.replace(temporary, STATE_FILE)


def main():
    connected = False
    left = None
    right = None

    objects = get_managed_objects()
    device = find_connected_device(objects)
    if device is not None:
        device_path, device_interfaces = device
        connected = True
        percentage = battery_percentage(device_interfaces)

        # (service_handle, level) sorted by handle, for each exported 0x2a19.
        readings = [
            (handle, read_level(char_path))
            for handle, char_path in battery_level_characters(objects, device_path)
        ]
        readable = [(handle, value) for handle, value in readings if value is not None]

        if percentage is not None:
            left = percentage
            if len(readable) >= 2:
                matching = [value for _, value in readable if value == percentage]
                other = [value for _, value in readable if value != percentage]
                if len(matching) == 1 and len(other) == 1:
                    right = other[0]
                else:
                    # Tie or ambiguous: lower handle is left, higher is right.
                    left = readable[0][1]
                    right = readable[1][1]
            # else: pre-flash, a single service -> right stays null.
        elif readable:
            # No Battery1 interface; fall back to the raw characteristics.
            left = readable[0][1]
            if len(readable) >= 2:
                right = readable[1][1]

    write_state(
        {
            "connected": connected,
            "left": left,
            "right": right,
            "updated": int(time.time()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
