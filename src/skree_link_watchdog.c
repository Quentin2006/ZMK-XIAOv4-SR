/*
 * Split link rate watchdog for the Skree Custom (central / left half only).
 *
 * The left half runs two 7.5 ms Bluetooth links on one radio: it is a
 * peripheral to the host (the PC sets that timing) and the central to the
 * right half (this half sets that timing). Both links start at a random slot
 * offset relative to each other. When their connection events overlap, the
 * radio serves only one link per slot, so the trackball reports forwarded by
 * zmk,input-split arrive in pairs in the same event and then skip one: an
 * effective 15 ms cadence that feels like a stutter. The overlap persists
 * because an update that keeps the same interval keeps the same anchor.
 *
 * Detection: while the ball moves, watch the arrival times of the sync'd input
 * reports on the trackball_split device (each sync marks the end of one sensor
 * frame). In the slow mode many frames arrive back to back, well under 2 ms
 * apart; when healthy they are spread about 7.5 ms apart. When more than 35%
 * of a 40-gap window arrives bunched, the link is retimed.
 *
 * Fix: change the split link's connection interval so the anchor moves.
 * Request 8.75 ms, then 7.5 ms again about 150 ms later. If three interval
 * changes in a row do not clear the slow mode, disconnect the split link once
 * so ZMK reconnects the halves with fresh timing - the same thing that
 * power-cycling the right half does.
 *
 * All Bluetooth calls run on the system workqueue, never in the input
 * callback, and nothing runs while the ball is idle (so there is no battery
 * cost at rest).
 */

#include <errno.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/device.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/spinlock.h>
#include <zephyr/sys/util.h>

LOG_MODULE_REGISTER(skree_link_watchdog, CONFIG_ZMK_LOG_LEVEL);

/* Right half's trackball reports, relayed by zmk,input-split. */
#define TRACKBALL_DEVICE DEVICE_DT_GET(DT_NODELABEL(trackball_split))

/* Motion window: this many inter-report gaps. */
#define WINDOW_GAPS 40
/* A longer gap means the ball paused: restart the window instead of measuring. */
#define MOTION_GAP_MS 50
/* Two frames closer than this arrived in the same radio event. */
#define BUNCHED_GAP_MS 2
/* Share of bunched gaps that means the link is in the slow mode. */
#define BUNCHED_PERCENT 35

/* Retry pacing: at least this far apart, at most this many per minute. */
#define MIN_ACTION_INTERVAL_MS 2000
#define MAX_ACTIONS_PER_MINUTE 10
/* How long a retime gets to prove itself before it counts as failed. */
#define TOGGLE_VERIFY_MS 10000
#define MAX_FAILED_TOGGLES 3
#define MIN_DISCONNECT_INTERVAL_MS 60000

/* Retiming sequence. Only the interval changes, which is what moves the anchor;
 * latency and timeout stay at ZMK's own split-link values. */
#define ANCHOR_INTERVAL 7 /* 8.75 ms */
#define FINAL_INTERVAL 6  /* 7.5 ms */
#define LINK_LATENCY 30
#define LINK_TIMEOUT 400
#define SETTLE_DELAY_MS 150
#define SETTLE_RETRIES 5
#define SETTLE_RETRY_MS 80

/* Host link insurance, checked once per connection 10 s after it comes up. */
#define HOST_CHECK_DELAY_MS 10000
#define HOST_MAX_INTERVAL FINAL_INTERVAL

static const struct bt_le_conn_param anchor_params = {
    .interval_min = ANCHOR_INTERVAL,
    .interval_max = ANCHOR_INTERVAL,
    .latency = LINK_LATENCY,
    .timeout = LINK_TIMEOUT,
};

static const struct bt_le_conn_param final_params = {
    .interval_min = FINAL_INTERVAL,
    .interval_max = FINAL_INTERVAL,
    .latency = LINK_LATENCY,
    .timeout = LINK_TIMEOUT,
};

enum watchdog_action {
    WATCHDOG_NONE,
    WATCHDOG_TOGGLE,
    WATCHDOG_DISCONNECT,
};

static struct k_spinlock state_lock;

/* Arrival-time window, fed by the input callback. */
static bool have_last_sample;
static int64_t last_sample_ms;
static uint8_t gap_count;
static uint8_t bunched_count;

/* Retiming bookkeeping, shared by the input callback and the workqueue. */
static int64_t last_action_ms;
static int64_t last_toggle_ms;
static int64_t last_disconnect_ms;
static int64_t minute_start_ms;
static uint8_t actions_this_minute;
static uint8_t failed_toggles;
static bool toggle_unverified;

/* Host link check: one delayed work per host connection. */
static struct bt_conn *host_conn;
static uint8_t settle_attempts;

static void retime_work_handler(struct k_work *work);
static K_WORK_DEFINE(retime_work, retime_work_handler);

static void settle_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(settle_work, settle_work_handler);

static void host_check_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(host_check_work, host_check_handler);

struct split_conn_search {
    struct bt_conn *conn;
};

static void find_split_conn(struct bt_conn *conn, void *data) {
    struct split_conn_search *search = data;
    struct bt_conn_info info;

    if (search->conn != NULL) {
        return;
    }
    if (bt_conn_get_info(conn, &info) < 0) {
        return;
    }
    if (info.role != BT_CONN_ROLE_CENTRAL || info.state != BT_CONN_STATE_CONNECTED) {
        return;
    }
    search->conn = bt_conn_ref(conn);
}

/* The split link is the one connection where this half is the central. */
static struct bt_conn *split_conn_ref(void) {
    struct split_conn_search search = {0};

    bt_conn_foreach(BT_CONN_TYPE_LE, find_split_conn, &search);
    return search.conn;
}

/* Pick what to do about a slow window. Called with state_lock held. */
static enum watchdog_action decide_action_locked(int64_t now) {
    if (now - last_action_ms < MIN_ACTION_INTERVAL_MS) {
        return WATCHDOG_NONE;
    }

    if (now - minute_start_ms >= 60000) {
        minute_start_ms = now;
        actions_this_minute = 0;
    }
    if (actions_this_minute >= MAX_ACTIONS_PER_MINUTE) {
        return WATCHDOG_NONE;
    }

    /* A retime that has been followed by another slow window did not work. */
    if (toggle_unverified) {
        toggle_unverified = false;
        if (now - last_toggle_ms <= TOGGLE_VERIFY_MS) {
            failed_toggles++;
        } else {
            failed_toggles = 0;
        }
    }

    if (failed_toggles >= MAX_FAILED_TOGGLES) {
        if (now - last_disconnect_ms < MIN_DISCONNECT_INTERVAL_MS) {
            return WATCHDOG_NONE;
        }
        return WATCHDOG_DISCONNECT;
    }

    return WATCHDOG_TOGGLE;
}

/* An interval change is the only thing that moves the link's anchor. */
static void request_anchor_change(struct bt_conn *conn, int64_t now) {
    int err = bt_conn_le_param_update(conn, &anchor_params);

    K_SPINLOCK(&state_lock) {
        last_action_ms = now;
        last_toggle_ms = now;
        actions_this_minute++;
        toggle_unverified = (err == 0 || err == -EALREADY);
    }

    if (err == 0) {
        LOG_INF("Slow trackball reports: split link moved to 8.75 ms, then back to 7.5 ms");
        k_work_schedule(&settle_work, K_MSEC(SETTLE_DELAY_MS));
    } else if (err == -EALREADY) {
        LOG_INF("Slow trackball reports: split link already at 8.75 ms, returning it to 7.5 ms");
        k_work_schedule(&settle_work, K_MSEC(SETTLE_DELAY_MS));
    } else {
        LOG_WRN("Could not retime the split link (%d)", err);
    }
}

static void disconnect_split_link(struct bt_conn *conn, int64_t now) {
    int err = bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);

    K_SPINLOCK(&state_lock) {
        last_action_ms = now;
        last_disconnect_ms = now;
        actions_this_minute++;
        failed_toggles = 0;
        toggle_unverified = false;
    }

    if (err < 0) {
        LOG_WRN("Could not disconnect the split link (%d)", err);
    } else {
        LOG_INF("Three retimes in a row did not fix the slow mode: disconnected the split link "
                "so the halves reconnect with fresh timing");
    }
}

static void retime_work_handler(struct k_work *work) {
    int64_t now = k_uptime_get();
    enum watchdog_action action;
    struct bt_conn *conn = split_conn_ref();

    if (conn == NULL) {
        LOG_DBG("Slow trackball reports, but the split link is not up; skipping");
        return;
    }

    K_SPINLOCK(&state_lock) { action = decide_action_locked(now); }

    switch (action) {
    case WATCHDOG_TOGGLE:
        request_anchor_change(conn, now);
        break;
    case WATCHDOG_DISCONNECT:
        disconnect_split_link(conn, now);
        break;
    default:
        LOG_DBG("Slow trackball reports, but the retime budget says wait");
        break;
    }

    bt_conn_unref(conn);
}

/* Second half of the retime: back to 7.5 ms once 8.75 ms has landed. */
static void settle_work_handler(struct k_work *work) {
    struct bt_conn *conn = split_conn_ref();
    struct bt_conn_info info;
    bool have_interval;
    uint16_t interval = 0;
    int err;

    if (conn == NULL) {
        LOG_DBG("Split link went away during retiming");
        settle_attempts = 0;
        return;
    }

    have_interval = bt_conn_get_info(conn, &info) == 0;
    if (have_interval) {
        interval = info.le.interval;
    }
    err = bt_conn_le_param_update(conn, &final_params);
    bt_conn_unref(conn);

    if (err == 0) {
        settle_attempts = 0;
        LOG_INF("Split link settled at 7.5 ms (was %u x 1.25 ms)", (unsigned int)interval);
        return;
    }
    if (err == -EALREADY) {
        if (have_interval && interval <= FINAL_INTERVAL) {
            settle_attempts = 0;
            LOG_INF("Split link confirmed at 7.5 ms");
            return;
        }
        /* The 8.75 ms update has not landed yet, so the link is still on the
         * old anchor; retry shortly instead of leaving it at 8.75 ms. */
        if (settle_attempts < SETTLE_RETRIES) {
            settle_attempts++;
            k_work_reschedule(&settle_work, K_MSEC(SETTLE_RETRY_MS));
            return;
        }
        settle_attempts = 0;
        LOG_WRN("Split link still at %u x 1.25 ms; the next slow window will retry",
                (unsigned int)interval);
        return;
    }

    settle_attempts = 0;
    LOG_WRN("Could not return the split link to 7.5 ms (%d)", err);
}

static void host_check_handler(struct k_work *work) {
    struct bt_conn *conn;
    struct bt_conn_info info;
    int err;

    K_SPINLOCK(&state_lock) {
        conn = host_conn;
        host_conn = NULL;
    }
    if (conn == NULL) {
        return;
    }

    err = bt_conn_get_info(conn, &info);
    if (err == 0 && info.state == BT_CONN_STATE_CONNECTED &&
        info.role == BT_CONN_ROLE_PERIPHERAL && info.le.interval > HOST_MAX_INTERVAL) {
        int update = bt_conn_le_param_update(conn, &final_params);
        LOG_INF("Host link is at %u x 1.25 ms; asked for 7.5 ms (%d)",
                (unsigned int)info.le.interval, update);
    } else if (err == 0) {
        LOG_INF("Host link is at %u x 1.25 ms", (unsigned int)info.le.interval);
    }

    bt_conn_unref(conn);
}

static void on_conn_connected(struct bt_conn *conn, uint8_t err) {
    struct bt_conn_info info;
    struct bt_conn *previous = NULL;

    if (err != 0) {
        return;
    }
    if (bt_conn_get_info(conn, &info) < 0) {
        return;
    }
    if (info.role != BT_CONN_ROLE_PERIPHERAL) {
        return; /* Only the host link; the split link is checked on detection. */
    }

    K_SPINLOCK(&state_lock) {
        previous = host_conn;
        host_conn = bt_conn_ref(conn);
    }
    if (previous != NULL) {
        bt_conn_unref(previous);
    }

    k_work_reschedule(&host_check_work, K_MSEC(HOST_CHECK_DELAY_MS));
}

static void on_conn_disconnected(struct bt_conn *conn, uint8_t reason) {
    struct bt_conn *mine = NULL;

    ARG_UNUSED(reason);

    K_SPINLOCK(&state_lock) {
        if (host_conn == conn) {
            mine = host_conn;
            host_conn = NULL;
        }
    }
    if (mine == NULL) {
        return;
    }

    k_work_cancel_delayable(&host_check_work);
    bt_conn_unref(mine);
}

BT_CONN_CB_DEFINE(skree_link_watchdog) = {
    .connected = on_conn_connected,
    .disconnected = on_conn_disconnected,
};

/* Each sync'd report is the end of one sensor frame forwarded by the right half. */
static void trackball_event_handler(struct input_event *evt) {
    int64_t now;
    bool request_retime = false;
    bool recovered = false;

    if (evt->sync == 0) {
        return;
    }

    now = k_uptime_get();

    K_SPINLOCK(&state_lock) {
        if (!have_last_sample) {
            have_last_sample = true;
            last_sample_ms = now;
        } else {
            int64_t gap = now - last_sample_ms;

            last_sample_ms = now;

            if (gap >= MOTION_GAP_MS) {
                /* The ball paused; this window is not about link timing. */
                gap_count = 0;
                bunched_count = 0;
            } else {
                if (gap < BUNCHED_GAP_MS) {
                    bunched_count++;
                }
                if (++gap_count >= WINDOW_GAPS) {
                    bool slow = ((uint16_t)bunched_count * 100U) >
                                ((uint16_t)WINDOW_GAPS * BUNCHED_PERCENT);

                    gap_count = 0;
                    bunched_count = 0;
                    if (slow) {
                        request_retime = true;
                    } else if (toggle_unverified) {
                        toggle_unverified = false;
                        failed_toggles = 0;
                        recovered = true;
                    }
                }
            }
        }
    }

    if (request_retime) {
        k_work_submit(&retime_work);
    }
    if (recovered) {
        LOG_INF("Trackball reports back at full rate after the link retime");
    }
}

INPUT_CALLBACK_DEFINE(TRACKBALL_DEVICE, trackball_event_handler);
