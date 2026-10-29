"""The hub sends planned command chains with durable counters."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.ble_mesh_lights.const import DOMAIN
from custom_components.ble_mesh_lights.planner import Target, plan
from custom_components.ble_mesh_lights.state import LampState

from .conftest import L1, VALEN, decode_sent, lamp_subentry, setup_entry, wait_for


async def _setup(hass, boards, hass_storage, **kwargs):
    entry = await setup_entry(hass, boards, lamps=[lamp_subentry()], hass_storage=hass_storage, **kwargs)
    hub = entry.runtime_data
    (sub_id,) = entry.subentries
    return entry, hub, sub_id


def _sent(board):
    return [decode_sent(call) for call in board.sent]


async def test_turn_on_sends_on_command(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_power(l1, True)
    assert _sent(fake_board) == [(0x7E01, 0, "on")]
    assert fake_board.sent[0]["repeat"] == 3.0 and fake_board.sent[0]["duration"] == 100.0
    assert hub.lamps[l1].state.on is True


async def test_target_runs_planned_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await hub.async_set_target(l1, Target(True, 2200, 100))
    assert _sent(fake_board) == [(0x7E01, 0, "energise"), (0x7E01, 1, "color_temperature")]
    assert hub.lamps[l1].state == LampState(True, 2200, 100)


async def test_commands_are_rate_limited(hass, fake_board, hass_storage, fake_clock, no_sleep):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_power(l1, True)
    fake_clock.now += 0.1
    await hub.async_power(l1, False)
    assert no_sleep == [pytest.approx(0.4)]


async def test_new_target_cancels_pending_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = LampState(True, 3000, 100)
    hub.set_assumed_state(l1, start)
    assert len(plan(VALEN, start, Target(True, 2200, 5))) >= 3
    first = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    await hub.async_set_target(l1, Target(True, 3000, 100))
    await first  # superseded: returns quietly
    state = hub.lamps[l1].state
    assert (state.on, state.color_temp, state.level) == (True, 3000, 100)
    assert [name for _, _, name in _sent(fake_board)].count("brightness_down") < 5


async def test_scene_cancels_pending_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    first = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    await hub.async_command(l1, "relax")
    await first
    assert _sent(fake_board)[-1][2] == "relax"
    assert len(fake_board.sent) <= 3
    assert hub.lamps[l1].state == LampState(True, 2700, 50, "relax")


async def test_send_fails_cleanly_without_board(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_board.offline = True  # unplugged, ESPHome has not noticed yet: the action raises
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, True)
    assert hub.lamps[l1].state.on is None
    fake_board.offline = False
    await hub.async_power(l1, True)
    assert _sent(fake_board) == [(0x7E01, 1, "on")]


async def test_no_ready_board_raises(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_board.set_connected(False)
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, True)
    assert fake_board.sent == []


async def test_counters_persist_and_resume(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_power(l1, True)
    await hub.async_power(l1, False)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_power(l1, True)
    assert _sent(fake_board)[-1][1] >= 256


async def test_missing_sequence_store_blocks_setup(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage, counters={}
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert ir.async_get(hass).async_get_issue(DOMAIN, "sequence_store_missing") is not None


async def test_state_restored_after_restart(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 30))
    hub.lamps[l1].uncertain = True
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    lamp = entry.runtime_data.lamps[l1]
    assert lamp.state == LampState(True, 2700, 30)
    assert lamp.uncertain is True


def _ticks(hub, fake_clock, seconds, step=0.5):
    for _ in range(int(seconds / step)):
        fake_clock.now += step
        hub.presence_tick()


async def test_choose_board_by_best_rssi(hass, make_board, hass_storage, fake_air, fake_clock):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    fake_air.rssi[L1] = {kitchen.device_id: -48, corridor.device_id: -71}
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(corridor.sent)) == (1, 0)
    fake_air.rssi[L1] = {kitchen.device_id: -80, corridor.device_id: -50}
    _ticks(hub, fake_clock, 35)  # a lasting change: past the smoothing and the minimum dwell
    await hub.async_power(l1, False)
    assert (len(kitchen.sent), len(corridor.sent)) == (1, 1)


async def test_board_fallback_when_nobody_hears_the_lamp(hass, make_board, hass_storage, fake_air):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    fake_air.rssi[L1] = {corridor.device_id: -60}
    await hub.async_power(l1, True)
    fake_air.rssi[L1] = {}
    await hub.async_power(l1, False)  # nobody reports RSSI now: the board that last did
    assert (len(kitchen.sent), len(corridor.sent)) == (0, 2)


async def test_offline_board_is_skipped(hass, make_board, hass_storage, fake_air, fake_clock):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    fake_air.rssi[L1] = {kitchen.device_id: -48, corridor.device_id: -71}
    kitchen.set_connected(False)
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(corridor.sent)) == (0, 1)

    kitchen.set_connected(True)
    await hass.async_block_till_done()
    _ticks(hub, fake_clock, 35)  # back on the board that hears the lamp best
    assert hub.lamps[l1].board == kitchen.device_id
    kitchen.offline = True  # the action fails: not repeated through the other board
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, False)
    assert len(corridor.sent) == 1


async def test_scene_clears_uncertainty(hass, fake_board, hass_storage):
    """A scene sets power, temperature and brightness at once: afterwards the state is known."""
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, None, None))
    hub.lamps[l1].uncertain = True
    await hub.async_command(l1, "brightness_up")
    assert hub.lamps[l1].uncertain is True  # a relative step keeps it uncertain
    await hub.async_command(l1, "relax")
    assert hub.lamps[l1].uncertain is False
    assert hub.lamps[l1].state == LampState(True, 2700, 50, "relax")


async def test_silent_board_is_skipped(hass, make_board, hass_storage, fake_air):
    """Unplugged, but HA has not noticed yet (its sensor still looks fine): the board's scanner has
    gone quiet, so commands go through the other board instead of being lost."""
    kitchen = make_board()
    street = make_board("ble-proxy-tx-street", "BLE Proxy TX (Street)")
    _, hub, l1 = await _setup(hass, [kitchen, street], hass_storage)
    fake_air.rssi[L1] = {kitchen.device_id: -45, street.device_id: -74}
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(street.sent)) == (1, 0)
    kitchen.silent = 20.0
    await hub.async_power(l1, False)
    assert (len(kitchen.sent), len(street.sent)) == (1, 1)
    assert hub.lamps[l1].board == street.device_id
