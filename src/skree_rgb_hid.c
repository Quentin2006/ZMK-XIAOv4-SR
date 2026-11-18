/*
 * Host-controlled RGB underglow for the Skree Custom.
 *
 * Listens for raw HID reports from the host and applies them through the
 * global `&rgb_ug` behavior, so the change reaches both split halves.
 *
 * Report layout (CONFIG_RAW_HID_REPORT_SIZE = 32, unused bytes ignored):
 *   byte 0: 'S' (0x53)
 *   byte 1: 'K' (0x4B)
 *   byte 2: command 0x01 = set HSB
 *   byte 3: hue high byte (0-359)
 *   byte 4: hue low byte
 *   byte 5: saturation (0-100)
 *   byte 6: brightness (0-100)
 */

#include <dt-bindings/zmk/rgb.h>

#include <zmk/behavior.h>
#include <zmk/event_manager.h>

#include <raw_hid/events.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(skree_rgb_hid);

#define SKREE_RGB_MAGIC_0 0x53
#define SKREE_RGB_MAGIC_1 0x4B
#define SKREE_RGB_CMD_SET_HSB 0x01

static int skree_rgb_hid_listener(const zmk_event_t *eh) {
    const struct raw_hid_received_event *event = as_raw_hid_received_event(eh);
    if (event == NULL || event->data == NULL || event->length < 7) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    const uint8_t *data = event->data;
    if (data[0] != SKREE_RGB_MAGIC_0 || data[1] != SKREE_RGB_MAGIC_1 ||
        data[2] != SKREE_RGB_CMD_SET_HSB) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    uint16_t hue = ((uint16_t)data[3] << 8) | data[4];
    uint8_t saturation = data[5];
    uint8_t brightness = data[6];

    if (hue > 360 || saturation > 100 || brightness > 100) {
        LOG_WRN("Ignoring out-of-range RGB report (%u, %u, %u)", hue, saturation, brightness);
        return ZMK_EV_EVENT_BUBBLE;
    }

    struct zmk_behavior_binding_event binding_event = {
        .layer = 0,
        .position = 0,
        .timestamp = k_uptime_get(),
    };

    struct zmk_behavior_binding color = {
        .behavior_dev = "rgb_ug",
        .param1 = RGB_COLOR_HSB_CMD,
        .param2 = RGB_COLOR_HSB_VAL(hue, saturation, brightness),
    };
    int err = zmk_behavior_invoke_binding(&color, binding_event, true);
    if (err < 0) {
        LOG_ERR("Failed to apply host RGB color (%d)", err);
        return ZMK_EV_EVENT_BUBBLE;
    }

    /* Spectrum/Swirl ignore the color. Force Solid (effect 0), which also
       saves the new state to flash so it survives sleep and reboots. */
    struct zmk_behavior_binding solid = {
        .behavior_dev = "rgb_ug",
        .param1 = RGB_EFS_CMD,
        .param2 = 0,
    };
    err = zmk_behavior_invoke_binding(&solid, binding_event, true);
    if (err < 0) {
        LOG_ERR("Failed to force solid RGB effect (%d)", err);
    }

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(skree_rgb_hid, skree_rgb_hid_listener);
ZMK_SUBSCRIPTION(skree_rgb_hid, raw_hid_received_event);
