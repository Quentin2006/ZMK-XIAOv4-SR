/*
 * Host-controlled RGB underglow for the Skree Custom.
 *
 * Applies a host color command through the global `&rgb_ug` behavior, so the
 * change reaches both split halves. Used by the USB raw HID listener and the
 * Bluetooth GATT service. See skree_rgb.h for the command layout.
 */

#include "skree_rgb.h"

#include <errno.h>

#include <dt-bindings/zmk/rgb.h>

#include <zmk/behavior.h>
#include <zmk/rgb_underglow.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(skree_rgb);

#define SKREE_RGB_MAGIC_0 0x53
#define SKREE_RGB_MAGIC_1 0x4B
#define SKREE_RGB_CMD_SET_HSB 0x01
#define SKREE_RGB_CMD_SET_HS 0x02

int skree_rgb_apply(const uint8_t *data, size_t len) {
    if (data == NULL || len < SKREE_RGB_CMD_LEN) {
        return 1;
    }
    if (data[0] != SKREE_RGB_MAGIC_0 || data[1] != SKREE_RGB_MAGIC_1) {
        return 1;
    }
    if (data[2] != SKREE_RGB_CMD_SET_HSB && data[2] != SKREE_RGB_CMD_SET_HS) {
        return 1;
    }

    uint16_t hue = ((uint16_t)data[3] << 8) | data[4];
    uint8_t saturation = data[5];
    if (hue > 360 || saturation > 100) {
        LOG_WRN("Ignoring out-of-range RGB command (%u, %u)", hue, saturation);
        return -EINVAL;
    }

    uint8_t brightness;
    if (data[2] == SKREE_RGB_CMD_SET_HSB) {
        brightness = data[6];
        if (brightness > 100) {
            return -EINVAL;
        }
    } else {
        /* Keep whatever the user last set with Fn+Tab / Fn+Shift. */
        brightness = zmk_rgb_underglow_calc_brt(0).b;
    }

    struct zmk_behavior_binding_event binding_event = {
        .layer = 0,
        .position = 0,
        .timestamp = k_uptime_get(),
    };

    /* Ensure both halves are lit even if one saved an "off" state. */
    struct zmk_behavior_binding on = {
        .behavior_dev = "rgb_ug",
        .param1 = RGB_ON_CMD,
    };
    zmk_behavior_invoke_binding(&on, binding_event, true);

    struct zmk_behavior_binding color = {
        .behavior_dev = "rgb_ug",
        .param1 = RGB_COLOR_HSB_CMD,
        .param2 = RGB_COLOR_HSB_VAL(hue, saturation, brightness),
    };
    int err = zmk_behavior_invoke_binding(&color, binding_event, true);
    if (err < 0) {
        LOG_ERR("Failed to apply host RGB color (%d)", err);
        return err;
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
        return err;
    }

    return 0;
}
