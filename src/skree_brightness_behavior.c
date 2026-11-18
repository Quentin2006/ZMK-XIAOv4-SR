/*
 * Brightness stepping behavior for the Skree Custom.
 *
 * &skree_brt 1 -> increase by 10%, capped at 100
 * &skree_brt 2 -> decrease by 10%, floored at 0
 *
 * The current level is read from ZMK's underglow state, so it stays in sync
 * with colors pushed from the host. Selecting Solid also saves the state.
 */

#define DT_DRV_COMPAT zmk_behavior_skree_brightness

#include <zephyr/device.h>
#include <zephyr/logging/log.h>

#include <drivers/behavior.h>
#include <dt-bindings/zmk/rgb.h>
#include <zmk/behavior.h>
#include <zmk/rgb_underglow.h>

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)

#define SKREE_BRT_STEP 10

static int on_pressed(struct zmk_behavior_binding *binding,
                      struct zmk_behavior_binding_event event) {
    struct zmk_led_hsb color = zmk_rgb_underglow_calc_brt(0);

    int level = color.b;
    if (binding->param1 == 2) {
        level -= SKREE_BRT_STEP;
        if (level < 0) {
            level = 0;
        }
    } else {
        level += SKREE_BRT_STEP;
        if (level > 100) {
            level = 100;
        }
    }
    color.b = (uint8_t)level;

    /* Make sure both halves are actually lit (a saved "off" state on the
       peripheral otherwise makes the change invisible there). */
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
