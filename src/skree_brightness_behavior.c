/*
 * Fine-grained brightness stepping behavior for the Skree Custom.
 *
 * &skree_brt 1 -> up:   +1 below 10%, then +10, capped at 100
 * &skree_brt 2 -> down: -10 down to 10%, then -1, floored at 0
 *
 * Declared the same way ZMK's own behaviors are (LOG_MODULE_DECLARE on the
 * shared "zmk" log module, BEHAVIOR_LOCALITY_GLOBAL, NULL init). Because the
 * locality is global, each split half runs the behavior on its own underglow
 * strip via the standard zmk_rgb_underglow_* API. Selecting Solid also
 * persists the new level to flash.
 */

#define DT_DRV_COMPAT zmk_behavior_skree_brightness

#include <zephyr/device.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include <drivers/behavior.h>
#include <zmk/behavior.h>
#include <zmk/rgb_underglow.h>

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

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
    color.b = (uint8_t)next_level(color.b, binding->param1 != 2);

    /* Make sure the strip is actually lit (a saved "off" state would
       otherwise make the change invisible). */
    zmk_rgb_underglow_on();

    int err = zmk_rgb_underglow_set_hsb(color);
    if (err < 0) {
        LOG_ERR("Failed to set brightness (%d)", err);
        return err;
    }
    return zmk_rgb_underglow_select_effect(0); /* Solid, and persist to flash */
}

static const struct behavior_driver_api skree_brightness_api = {
    .binding_pressed = on_pressed,
    .locality = BEHAVIOR_LOCALITY_GLOBAL,
};

BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,
                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &skree_brightness_api);

#endif /* DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT) */
