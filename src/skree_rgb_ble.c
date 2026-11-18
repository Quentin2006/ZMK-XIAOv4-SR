/*
 * Bluetooth transport for host-controlled RGB on the Skree Custom.
 *
 * BlueZ claims every HID-over-GATT service, so the raw HID interface is not
 * reachable over Bluetooth. This custom (non-HID) GATT service is left alone
 * by BlueZ and can be written from D-Bus. Same command layout as skree_rgb.h.
 *
 * Service:        c0de5000-5c3e-4b8a-9d4a-736b72656500
 * Characteristic: c0de5001-5c3e-4b8a-9d4a-736b72656500 (write, encrypted link)
 */

#include "skree_rgb.h"

#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(skree_rgb_ble);

#define SKREE_RGB_BLE_MAX_LEN 32

static struct bt_uuid_128 skree_rgb_service_uuid = BT_UUID_INIT_128(
    BT_UUID_128_ENCODE(0xc0de5000, 0x5c3e, 0x4b8a, 0x9d4a, 0x736b72656500));
static struct bt_uuid_128 skree_rgb_char_uuid = BT_UUID_INIT_128(
    BT_UUID_128_ENCODE(0xc0de5001, 0x5c3e, 0x4b8a, 0x9d4a, 0x736b72656500));

static uint8_t pending[SKREE_RGB_BLE_MAX_LEN];
static size_t pending_len;
static K_SPINLOCK_DEFINE(pending_lock);

static void skree_rgb_ble_work_handler(struct k_work *work) {
    uint8_t data[SKREE_RGB_BLE_MAX_LEN];
    size_t len;

    K_SPINLOCK(&pending_lock) {
        len = pending_len;
        memcpy(data, pending, len);
    }

    int err = skree_rgb_apply(data, len);
    if (err != 0) {
        LOG_WRN("Host RGB command not applied (%d)", err);
    }
}

static K_WORK_DEFINE(skree_rgb_ble_work, skree_rgb_ble_work_handler);

static ssize_t skree_rgb_ble_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                                   const void *buf, uint16_t len, uint16_t offset,
                                   uint8_t flags) {
    if (offset != 0) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }
    if (len < SKREE_RGB_CMD_LEN || len > SKREE_RGB_BLE_MAX_LEN) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }

    K_SPINLOCK(&pending_lock) {
        memcpy(pending, buf, len);
        pending_len = len;
    }

    k_work_submit(&skree_rgb_ble_work);
    return len;
}

BT_GATT_SERVICE_DEFINE(skree_rgb_svc, BT_GATT_PRIMARY_SERVICE(&skree_rgb_service_uuid),
                       BT_GATT_CHARACTERISTIC(&skree_rgb_char_uuid.uuid,
                                              BT_GATT_CHRC_WRITE |
                                                  BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                                              BT_GATT_PERM_WRITE_ENCRYPT, NULL,
                                              skree_rgb_ble_write, NULL), );
