/*
 * USB raw HID transport for host-controlled RGB on the Skree Custom.
 * Report size is CONFIG_RAW_HID_REPORT_SIZE (32); see skree_rgb.h.
 */

#include "skree_rgb.h"

#include <zmk/event_manager.h>

#include <raw_hid/events.h>

static int skree_rgb_hid_listener(const zmk_event_t *eh) {
    const struct raw_hid_received_event *event = as_raw_hid_received_event(eh);
    if (event != NULL) {
        skree_rgb_apply(event->data, event->length);
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(skree_rgb_hid, skree_rgb_hid_listener);
ZMK_SUBSCRIPTION(skree_rgb_hid, raw_hid_received_event);
