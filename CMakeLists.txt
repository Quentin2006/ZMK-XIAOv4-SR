zephyr_library()

# Raw HID is central-only; the peripheral has no HID endpoint to listen to.
if(CONFIG_RAW_HID)
  zephyr_library_sources(src/skree_rgb_hid.c)
endif()

zephyr_library_sources(src/skree_brightness_behavior.c)
