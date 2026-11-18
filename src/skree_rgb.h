/*
 * Shared host-to-keyboard RGB command handling for the Skree Custom.
 *
 * Command layout (at least 7 bytes, extra bytes ignored):
 *   byte 0: 'S' (0x53)
 *   byte 1: 'K' (0x4B)
 *   byte 2: command 0x01 = set HSB, 0x02 = set hue/saturation only
 *   byte 3: hue high byte (0-360)
 *   byte 4: hue low byte
 *   byte 5: saturation (0-100)
 *   byte 6: brightness (0-100, command 0x01 only)
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#define SKREE_RGB_CMD_LEN 7

/* Apply a host RGB command to both halves. Returns 0 on success, negative on
 * error, or 1 if the data is not a Skree RGB command. */
int skree_rgb_apply(const uint8_t *data, size_t len);
