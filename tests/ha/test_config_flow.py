"""Config flow and the settings options."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ble_mesh_lights.const import (
    CONF_BASE_ADDRESS,
    CONF_BOARDS,
    CONF_COMMAND_INTERVAL,
    CONF_OUTAGE_SENSOR,
    CONF_POWER_CYCLE_GAP,
    DATA_USED_INDICES,
    DOMAIN,
)

from .conftest import setup_entry


async def _user(hass, user_input):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_user_creates_entry(hass, fake_board):
    result = await _user(
        hass, {CONF_BOARDS: [fake_board.device_id], CONF_BASE_ADDRESS: "0x7E00", CONF_COMMAND_INTERVAL: 0.5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BLE Mesh Lights"
    assert result["data"] == {DATA_USED_INDICES: []}
    assert result["options"] == {
        CONF_BOARDS: [fake_board.device_id],
        CONF_BASE_ADDRESS: 0x7E00,
        CONF_COMMAND_INTERVAL: 0.5,
        CONF_POWER_CYCLE_GAP: 3.0,
    }
    await hass.async_block_till_done()
    assert fake_board.setups  # the entry loaded and set up the board


async def test_user_with_outage_sensor(hass, fake_board):
    result = await _user(
        hass,
        {
            CONF_BOARDS: [fake_board.device_id],
            CONF_BASE_ADDRESS: "7e00",
            CONF_COMMAND_INTERVAL: 1,
            CONF_OUTAGE_SENSOR: "binary_sensor.power_outage",
        },
    )
    assert result["options"][CONF_OUTAGE_SENSOR] == "binary_sensor.power_outage"
    assert result["options"][CONF_BASE_ADDRESS] == 0x7E00


async def test_single_instance(hass, fake_board):
    await setup_entry(hass, [fake_board])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_board_without_action_rejected(hass, fake_board):
    hass.services.async_remove("esphome", "ble_proxy_tx_kitchen_adv_svc_v1")
    result = await _user(
        hass, {CONF_BOARDS: [fake_board.device_id], CONF_BASE_ADDRESS: "0x7E00", CONF_COMMAND_INTERVAL: 0.5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BOARDS: "no_boards"}


async def test_no_board_selected(hass, fake_board):
    result = await _user(hass, {CONF_BOARDS: [], CONF_BASE_ADDRESS: "0x7E00", CONF_COMMAND_INTERVAL: 0.5})
    assert result["errors"] == {CONF_BOARDS: "no_boards"}


async def test_invalid_base_address(hass, fake_board):
    for text in ("0x9000", "zz", "0x0000"):
        result = await _user(
            hass, {CONF_BOARDS: [fake_board.device_id], CONF_BASE_ADDRESS: text, CONF_COMMAND_INTERVAL: 0.5}
        )
        assert result["errors"] == {CONF_BASE_ADDRESS: "invalid_base_address"}, text


async def _options_settings(hass, entry, user_input):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["type"] is FlowResultType.FORM
    form = result
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    await hass.async_block_till_done()
    return form, result


async def test_options_settings_change_reloads(hass, fake_board):
    entry = await setup_entry(hass, [fake_board])
    first_hub = entry.runtime_data
    _, result = await _options_settings(
        hass,
        entry,
        {CONF_BOARDS: [fake_board.device_id], CONF_BASE_ADDRESS: "0x7E00", CONF_COMMAND_INTERVAL: 1.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COMMAND_INTERVAL] == 1.0
    assert entry.runtime_data is not first_hub


async def test_adding_a_board_in_options(hass, make_board):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    entry = await setup_entry(hass, [kitchen])
    await _options_settings(
        hass,
        entry,
        {
            CONF_BOARDS: [kitchen.device_id, corridor.device_id],
            CONF_BASE_ADDRESS: "0x7E00",
            CONF_COMMAND_INTERVAL: 0.5,
        },
    )
    nodes = [board.node for board in entry.runtime_data.boards]
    assert nodes == ["ble-proxy-tx-kitchen", "ble-proxy-tx-corridor"]
    assert len(corridor.setups) == 1


async def test_base_address_locked_after_first_lamp(hass, fake_board):
    entry = await setup_entry(hass, [fake_board])
    hass.config_entries.async_update_entry(entry, data={DATA_USED_INDICES: [1]})
    form, result = await _options_settings(
        hass, entry, {CONF_BOARDS: [fake_board.device_id], CONF_COMMAND_INTERVAL: 0.5}
    )
    assert CONF_BASE_ADDRESS not in form["data_schema"].schema
    assert form["description_placeholders"]["base_address"] == "0x7E00"
    assert entry.options[CONF_BASE_ADDRESS] == 0x7E00


async def test_power_cycle_gap_in_settings(hass, fake_board):
    entry = await setup_entry(hass, [fake_board])
    await _options_settings(
        hass,
        entry,
        {
            CONF_BOARDS: [fake_board.device_id],
            CONF_BASE_ADDRESS: "0x7E00",
            CONF_COMMAND_INTERVAL: 0.5,
            CONF_POWER_CYCLE_GAP: 4.5,
        },
    )
    assert entry.options[CONF_POWER_CYCLE_GAP] == 4.5
    assert entry.runtime_data.power_cycle_gap == 4.5
