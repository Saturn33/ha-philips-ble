"""Names shared by the Home Assistant side of the integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ble_mesh_lights"

# Integration options
CONF_BOARDS = "boards"  # ESPHome device ids
CONF_BASE_ADDRESS = "base_address"
CONF_COMMAND_INTERVAL = "command_interval"
CONF_OUTAGE_SENSOR = "outage_sensor"
DEFAULT_BASE_ADDRESS = 0x7E00
DEFAULT_COMMAND_INTERVAL = 0.5
# Silence of a lamp in active mode (it sends several messages a second) that counts as a possible
# short switch-off at the wall.
CONF_POWER_CYCLE_GAP = "power_cycle_gap"
DEFAULT_POWER_CYCLE_GAP = 3.0

# Lamp subentries
SUBENTRY_LAMP = "lamp"
LAMP_PROFILE = "profile"
LAMP_MAC = "mac"
LAMP_ADDRESS = "address"
LAMP_INDEX = "index"
LAMP_AFTER_OUTAGE = "after_outage"
LAMP_ALIASES = "aliases"
LAMP_REMOTE = "remote"
AFTER_OUTAGE_NOTHING = "nothing"
AFTER_OUTAGE_RESTORE = "restore"

# Entry data: address indices ever handed out, never reused
DATA_USED_INDICES = "used_indices"

# The ble-adv-tx firmware package
RAW_ADV_EVENT = "esphome.ble_adv.raw_adv"
REQUIRED_PACKAGE_VERSION = 1
PACKAGE_SENSOR_NAME = "BLE TX package"
PACKAGE_SENSOR_OBJECT_ID = "ble_tx_package"
TRANSMIT_DURATION = 100  # ms
TRANSMIT_REPEAT = 3
IGNORED_DURATION = 20000.0  # ms the board ignores the echo of its own advert

STORAGE_SEQUENCE = f"{DOMAIN}.sequence"
STORAGE_STATE = f"{DOMAIN}.state"
STORAGE_VERSION = 1

PRESENCE_INTERVAL = 0.5  # seconds between beacon checks (short enough to see a 3 s silence)

SIGNAL_LAMP = f"{DOMAIN}_lamp_{{}}"
SIGNAL_REMOTE = f"{DOMAIN}_remote_{{}}"
SIGNAL_REMOTE_ADDED = f"{DOMAIN}_remote_added"

# Each entity task adds its platform.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SENSOR,
]
