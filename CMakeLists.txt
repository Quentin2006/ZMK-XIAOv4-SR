zephyr_library()

# Split-link rate watchdog. Central (left) half only: it notices when the
# trackball reports forwarded from the right half drop to every other radio
# event and re-times the split link. See src/skree_link_watchdog.c.
if(CONFIG_SKREE_LINK_WATCHDOG)
  zephyr_library_sources(src/skree_link_watchdog.c)
endif()
