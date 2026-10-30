"""Integration setup, unload, receiver setup on (re)connect, reload rules."""

from __future__ import annotations

import json

from homeassistant.config_entries import ConfigEntryState, ConfigSubentry

from custom_components.ble_mesh_lights.const import DATA_USED_INDICES
from custom_components.ble_mesh_lights.hub import Hub

from .conftest import ROOT, lamp_subentry, seed_counters, setup_entry

SETUP = {"ignored_duration": 20000.0, "ignored_cids": [], "ignored_macs": []}


async def test_setup_and_unload(hass, fake_board):
    entry = await setup_entry(hass, [fake_board])
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Hub)
    assert fake_board.setups == [SETUP]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_manifest_loads():
    manifest = json.loads((ROOT / "custom_components/ble_mesh_lights/manifest.json").read_text())
    assert manifest["single_config_entry"] is True
    # http and websocket_api: the remote card's files and its lamps command; frontend only when present.
    assert manifest["dependencies"] == ["bluetooth", "http", "websocket_api"]
    assert manifest["after_dependencies"] == ["esphome", "frontend"]


async def test_setup_receiver_called_again_on_reconnect(hass, fake_board):
    await setup_entry(hass, [fake_board])
    fake_board.set_connected(False)
    await hass.async_block_till_done()
    fake_board.set_connected(True)
    await hass.async_block_till_done()
    assert fake_board.setups == [SETUP, SETUP]


async def test_offline_board_does_not_fail_setup(hass, fake_board):
    fake_board.set_connected(False)
    entry = await setup_entry(hass, [fake_board])
    assert entry.state is ConfigEntryState.LOADED
    assert fake_board.setups == []


async def test_reconnect_sets_up_only_that_board(hass, make_board):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    await setup_entry(hass, [kitchen, corridor])
    corridor.set_connected(False)
    await hass.async_block_till_done()
    corridor.set_connected(True)
    await hass.async_block_till_done()
    assert len(kitchen.setups) == 1
    assert len(corridor.setups) == 2


async def test_subentry_change_reloads_but_used_indices_do_not(hass, fake_board, hass_storage):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    first_hub = entry.runtime_data
    hass.config_entries.async_update_entry(entry, data={**entry.data, DATA_USED_INDICES: [1]})
    await hass.async_block_till_done()
    assert entry.runtime_data is first_hub

    sub = lamp_subentry()
    seed_counters(hass_storage, {0x7E01: 0})
    hass.config_entries.async_add_subentry(entry, ConfigSubentry(**sub))
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first_hub
