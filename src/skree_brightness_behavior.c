/*
 * Brightness step behavior for the Skree Custom.
 *
 * &skree_brt 1 -> up:   +1 below 10%, then +10, capped at 100
 * &skree_brt 2 -> down: -10 down to 10%, then -1, floored at 0
 *
 * Runs on the central only and applies the new level through
 * skree_rgb_apply(), which invokes the global &rgb_ug behavior so both
 * halves change and the state is saved.
 */

#define DT_DRV_COMPAT zmk_behavior_skree_brightness

#include <zephyr/device.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <drivers/behavior.h>
#include <zmk/behavior.h>
#include <zmk/rgb_underglow.h>

#include "skree_rgb.h"

LOG_MODULE_REGISTER(skree_brightness);

#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)

static int next_level(int level, bool up) {
    if (up) {
        return level < 10 ? level + 1 : MIN(level + 10, 100);
    }
    return level > 10 ? MAX(level - 10, 10) : MAX(level - 1, 0);
}

static int on_pressed(struct zmk_behavior_binding *binding,
                      struct zmk_behavior_binding_event event) {
    struct zmk_led_hsb color = zmk_rgb_underglow_calc_brt(0);
    int level = next_level(color.b, binding->param1 != 2);

    uint8_t cmd[SKREE_RGB_CMD_LEN] = {
        'S', 'K', 0x01, (uint8_t)(color.h >> 8), (uint8_t)(color.h & 0xFF),
        color.s, (uint8_t)level,
    };
    int err = skree_rgb_apply(cmd, sizeof(cmd));
    if (err < 0) {
        LOG_ERR("Failed to set brightness %d (%d)", level, err);
        return err;
    }
    return ZMK_BEHAVIOR_OPAQUE;
}

static int on_released(struct zmk_behavior_binding *binding,
                       struct zmk_behavior_binding_event event) {
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api skree_brightness_api = {
    .binding_pressed = on_pressed,
    .binding_released = on_released,
    .locality = BEHAVIOR_LOCALITY_CENTRAL,
};

BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,
                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &skree_brightness_api);

#endif
