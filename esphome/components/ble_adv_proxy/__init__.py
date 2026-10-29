import esphome.codegen as cg
from esphome.components.esp32_ble import (
    CONF_BLE_ID,
    ESP32BLE,
    register_gap_event_handler,
    register_gap_scan_event_handler,
)
from esphome.components.text_sensor import new_text_sensor, text_sensor_schema
import esphome.config_validation as cv
from esphome.const import (
    CONF_ENTITY_CATEGORY,
    CONF_ID,
    CONF_NAME,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

AUTO_LOAD = ["esp32_ble", "esp32_ble_tracker", "bluetooth_proxy", "api", "text_sensor"]
DEPENDENCIES = ["esp32", "api"]
MULTI_CONF = False

bleadvproxy_ns = cg.esphome_ns.namespace("ble_adv_proxy")
BleAdvProxy = bleadvproxy_ns.class_(
    "BleAdvProxy", cg.Component, cg.Parented.template(ESP32BLE)
)

CONF_BLE_ADV_USE_MAX_TX_POWER = "use_max_tx_power"
CONF_PAUSE_SCAN_ON_SEND = "pause_scan_on_send"
CONF_NAME_SENSOR = "name_sensor"
CONF_VAL_NAME_SENSOR = "ble_adv_proxy_name"
CONF_ADAPTER_NAME = "adapter_name"

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(BleAdvProxy),
            cv.GenerateID(CONF_BLE_ID): cv.use_id(ESP32BLE),
            cv.Optional(CONF_BLE_ADV_USE_MAX_TX_POWER, default=False): cv.boolean,
            # Stop the BLE scan while adverts go out; resume it after the queue empties.
            cv.Optional(CONF_PAUSE_SCAN_ON_SEND, default=False): cv.boolean,
            cv.Optional(CONF_ADAPTER_NAME): cv.valid_name,
            cv.Optional(
                CONF_NAME_SENSOR,
                default={
                    CONF_ID: CONF_VAL_NAME_SENSOR,
                    CONF_NAME: CONF_VAL_NAME_SENSOR,
                    CONF_ENTITY_CATEGORY: ENTITY_CATEGORY_DIAGNOSTIC,
                },
            ): text_sensor_schema(),
        }
    ),
    # skylight-transmitter.yaml enables api custom_services and homeassistant_services.
    cv.require_esphome_version(2026, 4, 5),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_use_max_tx_power(config[CONF_BLE_ADV_USE_MAX_TX_POWER]))
    cg.add(var.set_pause_scan_on_send(config[CONF_PAUSE_SCAN_ON_SEND]))
    parent = await cg.get_variable(config[CONF_BLE_ID])
    register_gap_scan_event_handler(parent, var)
    register_gap_event_handler(parent, var)
    cg.add(var.set_parent(parent))
    sens = await new_text_sensor(config[CONF_NAME_SENSOR])
    cg.add(var.set_sensor_name(sens, config.get(CONF_ADAPTER_NAME, "")))
