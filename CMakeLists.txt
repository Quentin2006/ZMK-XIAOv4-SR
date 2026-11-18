zephyr_library()

# Host-controlled RGB lives on the central (left) half; it forwards changes to
# the peripheral through ZMK's global rgb_ug behavior.
if(CONFIG_RAW_HID OR CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
  zephyr_library_include_directories(src)
  zephyr_library_sources(src/skree_rgb.c)
endif()

# USB transport: raw HID (central only; the peripheral has no HID endpoint).
if(CONFIG_RAW_HID)
  zephyr_library_sources(src/skree_rgb_hid.c)
endif()

# Bluetooth transport: custom GATT service, central only.
if(CONFIG_ZMK_SPLIT_ROLE_CENTRAL AND CONFIG_ZMK_BLE)
  zephyr_library_sources(src/skree_rgb_ble.c)
endif()
