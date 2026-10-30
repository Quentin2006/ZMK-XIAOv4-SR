"""Validated configuration and deterministic ZMK/Hyprland generators."""
from pathlib import Path
import copy
import difflib
import hashlib
import json
import math
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "editor/config.json"
KEYMAP = "config/skreecustom.keymap"
MATRIX = "boards/shields/skreecustom/skreecustom.dtsi"
SENSOR = "boards/shields/skreecustom/skreecustom_right.overlay"
HOST = "desktop/hyprland/skree-control.lua"
KEYMAP_XKB = "desktop/hyprland/skree-swapped-slashes.xkb"
HWDB = "desktop/linux/99-skree-control.hwdb"
MANAGED = [CONFIG, KEYMAP, MATRIX, SENSOR, HOST, HWDB]
MOUSE_NAMES = ["skree-custom-mouse", "zmk-project-skree-custom-mouse", "zmk-project-skree-custom", "zmk-project-skree-custom-keyboard"]
HWDB_MATCHES = [
    "mouse:bluetooth:v1d50p615e:name:Skree Custom Mouse:*",
    "mouse:usb:v1d50p615e:name:ZMK Project Skree Custom Mouse:*",
    "mouse:usb:v1d50p615e:name:ZMK Project Skree Custom:*",
    "mouse:usb:v1d50p615e:name:ZMK Project Skree Custom Keyboard:*",
    "mouse:usb:v1d50p615e:name:Skree Custom Mouse:*",
]
LAYER_IDS = ["default_layer", "layer_1", "layer_2", "layer_3", "layer_4"]
KEY_CODES = set(json.loads((ROOT / "editor/static/keycodes.json").read_text()))
MODIFIERS = {"LC", "RC", "LS", "RS", "LA", "RA", "LG", "RG"}
MOUSE_CODES = {"LCLK", "RCLK", "MCLK", "MB4", "MB5"}
BT_ARGS = {"BT_CLR": 0, "BT_NXT": 0, "BT_PRV": 0, "BT_SEL": 1}
RGB_CODES = {"RGB_TOG", "RGB_ON", "RGB_OFF", "RGB_EFF", "RGB_EFR", "RGB_HUI", "RGB_HUD", "RGB_SAI", "RGB_SAD", "RGB_BRI", "RGB_BRD", "RGB_SPI", "RGB_SPD"}


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def exact_keys(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), f"Invalid {label} fields.")


def bounded(value, low, high, label, integer=False):
    require(type(value) in ([int] if integer else [int, float]) and math.isfinite(value) and low <= value <= high, f"{label} must be between {low} and {high}.")


def valid_key(value, depth=0):
    if value in KEY_CODES:
        return True
    if depth >= 4:
        return False
    match = re.fullmatch(r"([A-Z]{2})\((.+)\)", value)
    return bool(match and match[1] in MODIFIERS and valid_key(match[2], depth + 1))


def validate_binding(binding):
    require(isinstance(binding, str) and len(binding) <= 100 and "\n" not in binding, "Invalid key binding.")
    words = binding.split()
    require(bool(words) and re.fullmatch(r"&[a-z_]+", words[0]), "A binding must start with a supported ZMK behavior.")
    behavior, args = words[0][1:], words[1:]
    if behavior == "kp":
        require(len(args) == 1 and valid_key(args[0]), "Choose a valid ZMK key symbol or modifier expression.")
    elif behavior in {"mo", "to", "tog"}:
        require(len(args) == 1 and args[0] in {"0", "1", "2", "3", "4"}, "Layer must be 0–4.")
    elif behavior in {"trans", "none", "bootloader", "sys_reset"}:
        require(not args, "This behavior takes no arguments.")
    elif behavior == "mkp":
        require(len(args) == 1 and args[0] in MOUSE_CODES, "Choose a supported mouse button.")
    elif behavior == "rgb_ug":
        require(len(args) == 1 and args[0] in RGB_CODES, "Choose a supported lighting action.")
    elif behavior == "bt":
        require(bool(args) and args[0] in BT_ARGS and len(args) == 1 + BT_ARGS[args[0]], "Choose a supported Bluetooth action.")
        if args[0] == "BT_SEL":
            require(args[1] in {"0", "1", "2", "3", "4"}, "Bluetooth profile must be 0–4.")
    else:
        raise Invalid("Unsupported behavior; source is preserved until a supported binding is chosen.")
    return " ".join(words)


def validate(config):
    exact_keys(config, ["schemaVersion", "hardware", "firmware", "hyprland"], "configuration")
    require(type(config["schemaVersion"]) is int and config["schemaVersion"] == 1, "Unsupported configuration version.")
    require(config["hardware"] == {"profile": "skree-std-62-pmw3610", "vendorCommit": "487828c994aea35e24bdb38051a0ac7a4ef64c04", "keyCount": 62}, "The hardware profile cannot be changed in this editor.")
    fw = config["firmware"]
    exact_keys(fw, ["layers", "trackball"], "firmware")
    require(isinstance(fw["layers"], list) and len(fw["layers"]) == 5, "Exactly five firmware layers are required.")
    for i, layer in enumerate(fw["layers"]):
        exact_keys(layer, ["id", "name", "bindings"], "layer")
        require(layer["id"] == LAYER_IDS[i], "Layer identities must stay in matrix order.")
        require(isinstance(layer["name"], str) and 1 <= len(layer["name"]) <= 24 and not re.search(r"[\x00-\x1f]", layer["name"]), "Layer names must be 1–24 printable characters.")
        require(isinstance(layer["bindings"], list) and len(layer["bindings"]) == 62, "Each layer must contain all 62 key positions.")
        layer["bindings"] = [validate_binding(x) for x in layer["bindings"]]
    trackball = fw["trackball"]
    exact_keys(trackball, ["cpi", "autoMouseLayer", "autoMouseTimeoutMs"], "sensor")
    bounded(trackball["cpi"], 200, 3200, "Sensor CPI", True)
    require(trackball["cpi"] % 200 == 0, "Sensor CPI uses steps of 200.")
    require(type(trackball["autoMouseLayer"]) is int and trackball["autoMouseLayer"] == 3, "The auto mouse layer must remain layer 3.")
    bounded(trackball["autoMouseTimeoutMs"], 100, 5000, "Mouse-layer timeout", True)
    host = config["hyprland"]
    require(isinstance(host, dict), "Invalid desktop profile.")
    # Migrate historical profiles without inventing a DPI declaration for them.
    host.setdefault("accelProfile", "adaptive")
    host.setdefault("declaredCpi", 0)
    exact_keys(host, ["swapSlashes", "holdToScroll", "scrollButton", "middleClick", "scrollFactor", "naturalScroll", "sensitivity", "accelProfile", "declaredCpi"], "desktop profile")
    for key in ["swapSlashes", "holdToScroll", "naturalScroll"]:
        require(type(host[key]) is bool, f"{key} must be true or false.")
    require(host["scrollButton"] in {"left", "right", "middle"}, "Choose a scroll button.")
    require(host["middleClick"] in {"left-right-chord", "off"}, "Choose an available middle-click method.")
    bounded(host["scrollFactor"], 0.1, 5, "Scroll speed")
    bounded(host["sensitivity"], -1, 1, "Pointer sensitivity")
    require(host["accelProfile"] in {"adaptive", "flat"}, "Choose adaptive or flat acceleration.")
    bounded(host["declaredCpi"], 0, 3200, "Declared sensor CPI", True)
    require(host["declaredCpi"] == 0 or 200 <= host["declaredCpi"] and host["declaredCpi"] % 200 == 0, "Declared CPI must be zero (system default) or 200–3200 in steps of 200.")
    host["scrollFactor"] = float(host["scrollFactor"])
    host["sensitivity"] = float(host["sensitivity"])
    return config


def binding_spans(source):
    """Locate named layer binding bodies while rejecting missing/ambiguous layers."""
    # Existing vendor source has simple layer nodes; comments are masked so they
    # cannot inject fake declarations. Position lengths stay unchanged.
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " * len(m[0]), source, flags=re.S)
    result = []
    for layer in LAYER_IDS:
        matches = list(re.finditer(r"\b" + layer + r"\s*\{\s*bindings\s*=\s*<([^<>]*?)>\s*;", clean, re.S))
        require(len(matches) == 1, f"Cannot safely find bindings for {layer}.")
        result.append(matches[0].span(1))
    return result


def parse_keymap(source):
    result = []
    for start, end in binding_spans(source):
        body = re.sub(r"/\*.*?\*/|//[^\n]*", " ", source[start:end], flags=re.S)
        bindings = re.findall(r"&[a-z_]+(?:\s+(?!&)[A-Z0-9_()]+)*", body)
        require(len(bindings) == 62, "Expected 62 bindings in each vendor layer.")
        require(not re.sub(r"&[a-z_]+(?:\s+(?!&)[A-Z0-9_()]+)*", "", body).strip(), "Unrecognized keymap syntax.")
        result.append([validate_binding(x) for x in bindings])
    return result


def render_keymap(source, layers):
    spans = binding_spans(source)
    for (start, end), layer in reversed(list(zip(spans, layers))):
        values = layer["bindings"]
        lines = []
        for row in range(4):
            group = values[row * 12:row * 12 + 12]
            lines.append(("    " + "  ".join(x.ljust(18) for x in group[:6]) + "    " + "  ".join(x.ljust(18) for x in group[6:])).rstrip())
        for a, b in [(48, 52), (52, 56), (56, 62)]:
            lines.append("    " + "  ".join(values[a:b]))
        body = "\n" + "\n".join(lines) + "\n            "
        source = source[:start] + body + source[end:]
    return source


def render_host(config):
    h = config["hyprland"]
    boolean = lambda value: "true" if value else "false"
    keymap = 'os.getenv("HOME") .. "/.config/hypr/keymaps/skree-swapped-slashes.xkb"' if h["swapSlashes"] else '""'
    button = {"left": 272, "right": 273, "middle": 274}[h["scrollButton"]]
    return f'''-- Generated by Skree Control. Edit editor/config.json or the local web editor.
-- This is a computer-only profile; firmware changes require a separate flash.
local keymap = {keymap}
for _, name in ipairs({{ "skree-custom-keyboard", "zmk-project-skree-custom-keyboard" }}) do
  hl.device({{ name = name, kb_file = keymap }})
end
for _, name in ipairs({{ {', '.join(json.dumps(n) for n in MOUSE_NAMES)} }}) do
  hl.device({{
    name = name,
    middle_button_emulation = {boolean(h["middleClick"] == "left-right-chord")},
    scroll_method = "{"on_button_down" if h["holdToScroll"] else "no_scroll"}",
    scroll_button = {button},
    scroll_button_lock = false,
    scroll_factor = {h["scrollFactor"]},
    natural_scroll = {boolean(h["naturalScroll"])},
    sensitivity = {h["sensitivity"]},
    accel_profile = "{h['accelProfile']}",
  }})
end
'''


def render_hwdb(config):
    header = "# Generated by Skree Control. Declare the CPI actually flashed on the sensor.\n"
    cpi = config["hyprland"]["declaredCpi"]
    if cpi == 0:
        return header + "# No DPI override; use the system default.\n"
    return header + "\n".join(HWDB_MATCHES) + f"\n MOUSE_DPI={cpi}\n"


def generated(config, root=ROOT):
    config = validate(copy.deepcopy(config))
    matrix = (root / MATRIX).read_text()
    matrix, count = re.subn(r"<&zip_temp_layer\s+3\s+\d+>", f'<&zip_temp_layer 3 {config["firmware"]["trackball"]["autoMouseTimeoutMs"]}>', matrix)
    require(count == 1, "Cannot safely find the mouse-layer processor.")
    sensor, count = re.subn(r"\bcpi\s*=\s*<\d+>", f'cpi = <{config["firmware"]["trackball"]["cpi"]}>', (root / SENSOR).read_text())
    require(count == 1, "Cannot safely find the trackball CPI.")
    return {CONFIG: json.dumps(config, indent=2) + "\n", KEYMAP: render_keymap((root / KEYMAP).read_text(), config["firmware"]["layers"]), MATRIX: matrix, SENSOR: sensor, HOST: render_host(config), HWDB: render_hwdb(config)}


def changes(before, after):
    result = []
    for layer_index, layer in enumerate(after["firmware"]["layers"]):
        old = before["firmware"]["layers"][layer_index]
        if old["name"] != layer["name"]:
            result.append({"scope": "firmware", "label": f"Layer {layer_index} name", "before": old["name"], "after": layer["name"]})
        for i, binding in enumerate(layer["bindings"]):
            if binding != old["bindings"][i]:
                result.append({"scope": "firmware", "label": f'{layer["name"]} · key {i + 1}', "before": old["bindings"][i], "after": binding})
    for scope, old, new in [("firmware", before["firmware"]["trackball"], after["firmware"]["trackball"]), ("desktop", before["hyprland"], after["hyprland"])]:
        for key, value in new.items():
            if old[key] != value:
                result.append({"scope": scope, "label": key, "before": old[key], "after": value})
    return result


def preview(config, root=ROOT):
    before = validate(json.loads((root / CONFIG).read_text()))
    after = validate(copy.deepcopy(config))
    files = generated(after, root)
    diffs = []
    for path, content in files.items():
        old = (root / path).read_text() if (root / path).exists() else ""
        if old != content:
            diffs.append({"path": path, "diff": "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile="a/" + path, tofile="b/" + path))})
    return {"changes": changes(before, after), "files": diffs, "firmwareRequiresFlash": before["firmware"] != after["firmware"]}


def checksum(root=ROOT):
    hasher = hashlib.sha256()
    for name in MANAGED:
        hasher.update(name.encode())
        if (root / name).exists():
            hasher.update((root / name).read_bytes())
    return hasher.hexdigest()
