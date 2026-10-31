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
    assert hub.lamps[l1].state == LampState(True, 2700, 50)


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
    assert hub.lamps[l1].state == LampState(True, 2700, 50)


async def test_unload_waits_for_a_running_chain(hass, fake_board, hass_storage):
    """The command on the air when the entry unloads finishes, and the saved state includes it."""
    from custom_components.ble_mesh_lights.const import STORAGE_STATE
    from custom_components.ble_mesh_lights.state import apply

    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = LampState(True, 3000, 100)
    hub.set_assumed_state(l1, start)
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await chain
    sent = len(fake_board.sent)
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(fake_board.sent) == sent  # nothing after the unload
    expected = start
    for _, _, name in _sent(fake_board):
        expected = apply(VALEN, expected, name)
    stored = hass_storage[STORAGE_STATE]["data"]["lamps"][l1]
    assert (stored["on"], stored["color_temp"], stored["level"]) == (
        expected.on,
        expected.color_temp,
        expected.level,
    )


async def test_superseded_chain_sends_nothing_after_its_wait(hass, fake_board, hass_storage, fake_clock):
    """A chain waiting out the command interval when a newer request comes sends nothing more."""
    from custom_components.ble_mesh_lights.state import apply

    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = LampState(True, 3000, 100)
    hub.set_assumed_state(l1, start)
    first_target, second_target = Target(True, 2200, 5), Target(True, 6500, 100)
    first = asyncio.ensure_future(hub.async_set_target(l1, first_target))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    await hub.async_set_target(l1, second_target)
    await first
    first_command = plan(VALEN, start, first_target)[0]
    expected = [first_command, *plan(VALEN, apply(VALEN, start, first_command), second_target)]
    assert [name for _, _, name in _sent(fake_board)] == expected


async def test_closed_hub_refuses_to_send(hass, fake_board, hass_storage):
    from custom_components.ble_mesh_lights.const import STORAGE_SEQUENCE
    from custom_components.ble_mesh_lights.hub import HubClosed

    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = dict(hass_storage[STORAGE_SEQUENCE]["data"]["addresses"])
    with pytest.raises(HubClosed):
        await hub.async_power(l1, True)
    assert fake_board.sent == []
    assert hass_storage[STORAGE_SEQUENCE]["data"]["addresses"] == stored


async def test_board_on_package_1_still_sends(hass, make_board, hass_storage, freezer):
    from datetime import timedelta

    board = make_board(version="1")
    _, hub, l1 = await _setup(hass, [board], hass_storage)
    freezer.tick(timedelta(minutes=5))  # package 1 reports once a minute or less: no heartbeat check
    await hub.async_power(l1, True)
    assert len(board.sent) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"board_outdated_{board.device_id}") is not None


async def test_set_assumed_state_stops_a_running_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    hub.set_assumed_state(l1, LampState(True, 4000, 80))  # the user corrects HA's idea of the lamp
    await chain
    assert len(fake_board.sent) <= 2  # the command on the air finishes, nothing after it
    assert hub.lamps[l1].state == LampState(True, 4000, 80)


async def _beats(hass, fake_clock, boards, seconds, step=10.0):
    for _ in range(int(seconds / step)):
        fake_clock.now += step
        for board in boards:
            board.beat()
    await hass.async_block_till_done()


async def test_beating_board_stays_ready(hass, make_board, hass_storage, fake_clock):
    """An hour with no commands and no BLE traffic around: the heartbeat keeps the board in use."""
    board = make_board()
    _, hub, l1 = await _setup(hass, [board], hass_storage)
    await _beats(hass, fake_clock, [board], 3600)
    await hub.async_power(l1, True)
    assert len(board.sent) == 1


async def test_board_without_heartbeat_is_skipped(hass, make_board, hass_storage, fake_air, fake_clock):
    """Unplugged, but HA has not noticed the lost connection yet: after 25 s without a heartbeat
    the other board is used."""
    kitchen = make_board()
    street = make_board("ble-proxy-tx-street", "BLE Proxy TX (Street)")
    _, hub, l1 = await _setup(hass, [kitchen, street], hass_storage)
    fake_air.rssi[L1] = {kitchen.device_id: -45, street.device_id: -74}
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(street.sent)) == (1, 0)
    kitchen.beating = False  # unplugged: no heartbeat, its sensor still looks fine
    await _beats(hass, fake_clock, [street], 40)
    await hub.async_power(l1, False)
    assert (len(kitchen.sent), len(street.sent)) == (1, 1)
    # Plugged back in: it beats again, and once the lamp's board rules allow (30 s on one board,
    # 6 dB better) the lamp moves back to the board that hears it best.
    kitchen.beating = True
    active = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8000001000000000000")
    for _ in range(40):
        fake_clock.now += 1
        fake_air.beacon(L1, active, fake_clock.now)
        hub.presence_tick()
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(street.sent)) == (2, 1)


async def test_new_board_has_time_for_its_first_beat(hass, make_board, hass_storage, fake_clock):
    board = make_board()
    board.beating = False
    _, hub, l1 = await _setup(hass, [board], hass_storage)
    await hub.async_power(l1, True)  # no beat yet, just set up
    assert len(board.sent) == 1
    fake_clock.now += 36
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, False)


async def test_sensor_updates_are_no_heartbeat(hass, make_board, hass_storage, fake_clock):
    """Round 1 used the sensor's last_reported; HA's ESPHome integration drops unchanged states,
    so re-written sensor states must not count."""
    board = make_board()
    board.beating = False
    _, hub, l1 = await _setup(hass, [board], hass_storage)
    for _ in range(4):  # 40 s, past the 35 s timeout
        fake_clock.now += 10
        hass.states.async_set(board.version_entity_id, board.version)
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, True)


async def test_chain_superseded_while_waiting_for_the_board_sends_nothing(hass, fake_board, hass_storage):
    """Two lamps on one board: lamp B's chain waits for the board while A sends. B is re-targeted
    meanwhile, so B's old command must not go out once the board is free."""
    lamps = [lamp_subentry(), lamp_subentry("Hall", mac="E8:26:CF:00:00:02", address=0x7E02, index=2)]
    entry = await setup_entry(hass, [fake_board], lamps=lamps, hass_storage=hass_storage)
    hub = entry.runtime_data
    ids = {sub.title: sub_id for sub_id, sub in entry.subentries.items()}
    street, hall = ids["Street"], ids["Hall"]
    hub.set_assumed_state(hall, LampState(True, 3000, 100))
    fake_board.hold = asyncio.Event()
    a = asyncio.ensure_future(hub.async_power(street, True))
    await wait_for(lambda: hub._board_locks and next(iter(hub._board_locks.values())).locked())
    b_old = asyncio.ensure_future(hub.async_command(hall, "relax"))
    for _ in range(5):
        await asyncio.sleep(0)
    b_new = asyncio.ensure_future(hub.async_power(hall, False))
    for _ in range(5):
        await asyncio.sleep(0)
    fake_board.hold.set()
    await asyncio.gather(a, b_old, b_new)
    sent = [(source, name) for source, _, name in _sent(fake_board)]
    assert sent == [(0x7E01, "on"), (0x7E02, "off")]


async def test_two_missed_heartbeats_keep_the_board(hass, make_board, hass_storage, fake_clock):
    """Beats every 10 s, timeout 35 s: two lost beats in a row do not drop the board."""
    board = make_board()
    board.beating = False
    _, hub, l1 = await _setup(hass, [board], hass_storage)
    fake_clock.now += 30
    await hub.async_power(l1, True)
    assert len(board.sent) == 1
    fake_clock.now += 6
    with pytest.raises(HomeAssistantError):
        await hub.async_power(l1, False)


async def test_quick_presses_all_sent(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 30))
    await asyncio.gather(*(hub.async_press(l1, "brightness_up") for _ in range(3)))
    assert [name for _, _, name in _sent(fake_board)] == ["brightness_up"] * 3
    assert hub.lamps[l1].state == LampState(True, 3000, 100)  # 30 → 50 → 80 → 100


async def test_press_cancels_running_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    assert len(plan(VALEN, LampState(True, 3000, 100), Target(True, 2200, 5))) >= 3
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    await hub.async_press(l1, "off")
    await chain
    names = [name for _, _, name in _sent(fake_board)]
    assert names[-1] == "off" and len(names) <= 3
    assert hub.lamps[l1].state.on is False


async def test_press_queue_limit(hass, fake_board, hass_storage):
    from custom_components.ble_mesh_lights.hub import PressQueueFull

    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    presses = [asyncio.ensure_future(hub.async_press(l1, "color_temperature")) for _ in range(10)]
    await asyncio.sleep(0)  # the ten presses are accepted
    with pytest.raises(PressQueueFull):
        await hub.async_press(l1, "color_temperature")
    await asyncio.gather(*presses)
    assert len(fake_board.sent) == 10


async def test_press_queue_recovers_after_failures(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_board.offline = True
    results = await asyncio.gather(*(hub.async_press(l1, "on") for _ in range(10)), return_exceptions=True)
    assert all(isinstance(result, HomeAssistantError) for result in results)
    fake_board.offline = False
    await hub.async_press(l1, "on")
    assert [name for _, _, name in _sent(fake_board)] == ["on"]


async def test_failed_press_does_not_stop_the_next(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_board.offline = True
    first = asyncio.ensure_future(hub.async_press(l1, "off"))
    second = asyncio.ensure_future(hub.async_press(l1, "on"))
    await asyncio.sleep(0)
    with pytest.raises(HomeAssistantError):
        await first
    fake_board.offline = False
    await second
    assert [name for _, _, name in _sent(fake_board)] == ["on"]


async def test_chain_after_presses_waits(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    presses = [asyncio.ensure_future(hub.async_press(l1, "brightness_down")) for _ in range(2)]
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 3000, 30)))
    await asyncio.gather(*presses, chain)
    names = [name for _, _, name in _sent(fake_board)]
    assert names[:2] == ["brightness_down", "brightness_down"]
    assert names[2:] == ["brightness_down"]  # planned from 50 %, where the presses left it
    assert hub.lamps[l1].state == LampState(True, 3000, 30)


async def test_queued_presses_fail_on_unload(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_board.hold = asyncio.Event()
    presses = [asyncio.ensure_future(hub.async_press(l1, "on")) for _ in range(3)]
    await wait_for(lambda: fake_board.hold is not None and len(presses) == 3)
    await asyncio.sleep(0)
    unload = asyncio.ensure_future(hass.config_entries.async_unload(entry.entry_id))
    await asyncio.sleep(0)
    fake_board.hold.set()
    results = await asyncio.gather(*presses, return_exceptions=True)
    assert await unload
    assert len(fake_board.sent) <= 1
    from custom_components.ble_mesh_lights.hub import HubClosed

    assert all(isinstance(result, HubClosed) for result in results[1:])
