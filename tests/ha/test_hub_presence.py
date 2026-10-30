"""The hub detects power from beacons and restores lamps after outages."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.ble_mesh_lights.const import CONF_OUTAGE_SENSOR, CONF_POWER_CYCLE_GAP
from custom_components.ble_mesh_lights.planner import Target
from custom_components.ble_mesh_lights.state import LampState

from .conftest import L1, decode_sent, fire_raw_adv, lamp_subentry, remote_press, setup_entry

ACTIVE = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8000001000000000000")
QUIET = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8010001000000000000")
SCAN_RESPONSE = bytes.fromhex("0cff01ffffffffad6554cf26e8")
OUTAGE = "binary_sensor.power_outage"


async def _setup(hass, boards, hass_storage, options=None, **lamp):
    entry = await setup_entry(
        hass, boards, lamps=[lamp_subentry(**lamp)], hass_storage=hass_storage, options=options
    )
    (sub_id,) = entry.subentries
    return entry, entry.runtime_data, sub_id


async def _tick(hass, hub, clock, until, step=2.0):
    """Presence ticks every `step` seconds up to `until`."""
    while clock.now + step <= until + 1e-9:
        clock.now += step
        hub.presence_tick()
    await hass.async_block_till_done()


async def test_active_lamp_present_and_rssi(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_air.beacon(L1, ACTIVE, fake_clock.now, rssi=-52, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    lamp = hub.lamps[l1]
    assert lamp.present is True
    assert (lamp.rssi, lamp.board) == (-52, fake_board.device_id)


async def test_switch_off_makes_unavailable_and_back_restores(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    start = fake_clock.now
    fake_air.beacon(L1, ACTIVE, start, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 12)
    lamp = hub.lamps[l1]
    assert lamp.present is False
    assert lamp.before_loss == LampState(False, 2700, 50)
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    assert lamp.present is True
    assert lamp.state == LampState(True, 3000, 100)
    assert lamp.uncertain is False


async def test_stale_advert_after_loss_does_not_restore(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    start = fake_clock.now
    fake_air.beacon(L1, ACTIVE, start, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 72)  # HA keeps returning the same advert
    lamp = hub.lamps[l1]
    assert lamp.present is False
    assert lamp.before_loss == LampState(False, 2700, 50)
    assert lamp.state == LampState(False, 2700, 50)


async def test_scan_response_reuses_last_flag(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = fake_clock.now
    fake_air.beacon(L1, QUIET, start, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 2)
    fake_air.beacon(L1, SCAN_RESPONSE, start + 60, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 240)  # quiet timeout counts from the scan response
    assert hub.lamps[l1].present is True


async def test_quiet_lamp_not_lost_between_bursts(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = fake_clock.now
    for minute in range(4):
        fake_air.beacon(L1, QUIET, start + 60 * minute, board=fake_board.device_id)
        await _tick(hass, hub, fake_clock, start + 60 * minute + 58)
        assert hub.lamps[l1].present is True


async def test_unpowered_lamp_unavailable_after_start(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = fake_clock.now
    await _tick(hass, hub, fake_clock, start + 188)
    assert hub.lamps[l1].present is None
    await _tick(hass, hub, fake_clock, start + 192)
    assert hub.lamps[l1].present is False


async def test_first_active_beacon_after_start_marks_uncertain(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    fake_air.beacon(L1, ACTIVE, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert hub.lamps[l1].state == LampState(False, 2700, 50)
    assert hub.lamps[l1].uncertain is True


async def test_first_quiet_beacon_after_start_keeps_certainty(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    fake_air.beacon(L1, QUIET, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert hub.lamps[l1].uncertain is False


async def test_lost_lamp_stays_lost_after_reload(hass, fake_board, hass_storage, fake_air, fake_clock):
    """A lamp switched off at the wall stays "no power" across a reload of the entry: HA still
    holds its last advert from before the switch-off, which is no new sighting."""
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = fake_clock.now
    fake_air.beacon(L1, ACTIVE, start, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 2)
    hub.set_assumed_state(l1, LampState(True, 2700, 10))  # known, not uncertain
    await _tick(hass, hub, fake_clock, start + 12)
    assert hub.lamps[l1].present is False
    fake_air.adverts.pop(L1)  # HA dropped the stale advert meanwhile: nothing tells the lamp is off
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hub = entry.runtime_data
    await _tick(hass, hub, fake_clock, fake_clock.now + 6)
    lamp = hub.lamps[l1]
    assert lamp.present is False
    assert lamp.uncertain is False
    assert hass.states.get("light.street").state == "unavailable"
    # Power comes back: an active beacon newer than the one held.
    fake_air.beacon(L1, ACTIVE, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert lamp.present is True
    assert lamp.state == LampState(True, 3000, 100)


async def test_cached_advert_at_start_is_no_sighting(hass, fake_board, hass_storage, fake_air, fake_clock):
    """An advert HA held before the entry started proves nothing about the lamp now."""
    fake_air.beacon(L1, ACTIVE, fake_clock.now - 100, board=fake_board.device_id)
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await _tick(hass, hub, fake_clock, fake_clock.now + 6)
    lamp = hub.lamps[l1]
    assert lamp.present is None
    assert lamp.uncertain is False
    fake_air.beacon(L1, QUIET, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert lamp.present is True


async def test_lamp_heard_only_by_second_board(hass, make_board, hass_storage, fake_air, fake_clock):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    fake_air.beacon(L1, ACTIVE, fake_clock.now, rssi=-77, board=corridor.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    lamp = hub.lamps[l1]
    assert (lamp.present, lamp.rssi, lamp.board) == (True, -77, corridor.device_id)
    await hub.async_power(l1, True)
    assert (len(kitchen.sent), len(corridor.sent)) == (0, 1)


async def _lose_power(hass, hub, l1, fake_air, fake_clock, board):
    start = fake_clock.now
    fake_air.beacon(L1, ACTIVE, start, board=board.device_id)
    await _tick(hass, hub, fake_clock, start + 12)
    assert hub.lamps[l1].present is False
    return start


async def test_outage_restores_previous_state(hass, fake_board, hass_storage, fake_air, fake_clock):
    hass.states.async_set(OUTAGE, "off")
    _, hub, l1 = await _setup(
        hass, [fake_board], hass_storage, options={CONF_OUTAGE_SENSOR: OUTAGE}, after_outage="restore"
    )
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    hass.states.async_set(OUTAGE, "on")
    await hass.async_block_till_done()
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    assert [decode_sent(call)[2] for call in fake_board.sent] == ["off"]
    assert hub.lamps[l1].state.on is False


async def test_outage_restores_an_on_lamp(hass, fake_board, hass_storage, fake_air, fake_clock):
    hass.states.async_set(OUTAGE, "off")
    _, hub, l1 = await _setup(
        hass, [fake_board], hass_storage, options={CONF_OUTAGE_SENSOR: OUTAGE}, after_outage="restore"
    )
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    hass.states.async_set(OUTAGE, "on")
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    state = hub.lamps[l1].state
    assert (state.on, state.color_temp, state.level) == (True, 2700, 50)


async def test_single_switch_cycle_not_restored(hass, fake_board, hass_storage, fake_air, fake_clock):
    hass.states.async_set(OUTAGE, "off")
    _, hub, l1 = await _setup(
        hass, [fake_board], hass_storage, options={CONF_OUTAGE_SENSOR: OUTAGE}, after_outage="restore"
    )
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    assert fake_board.sent == []


async def test_lamp_without_restore_option_not_restored(hass, fake_board, hass_storage, fake_air, fake_clock):
    hass.states.async_set(OUTAGE, "off")
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, options={CONF_OUTAGE_SENSOR: OUTAGE})
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    hass.states.async_set(OUTAGE, "on")
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    assert fake_board.sent == []


async def test_outage_sensor_on_at_start_restores_stored_state(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    """HA restarted with the lamps (no UPS): the hub never saw the loss."""
    hass.states.async_set(OUTAGE, "on")
    hass.states.get(OUTAGE).last_changed  # noqa: B018  (set just now)
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage, after_outage="restore")
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_OUTAGE_SENSOR: OUTAGE})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub = entry.runtime_data
    fake_air.beacon(L1, ACTIVE, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert [decode_sent(call)[2] for call in fake_board.sent] == ["off"]


async def test_old_outage_at_start_ignored(hass, fake_board, hass_storage, fake_air, fake_clock, freezer):
    hass.states.async_set(OUTAGE, "on")
    freezer.tick(timedelta(minutes=11))
    assert dt_util.utcnow() - hass.states.get(OUTAGE).last_changed > timedelta(minutes=10)
    _, hub, l1 = await _setup(
        hass, [fake_board], hass_storage, options={CONF_OUTAGE_SENSOR: OUTAGE}, after_outage="restore"
    )
    fake_air.beacon(L1, ACTIVE, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert fake_board.sent == []


async def test_pairing_after_single_restore_binds_remote(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    fake_clock.now += 30
    fire_raw_adv(hass, remote_press("pairing", 0x13B3, 120), fake_board.device_id)
    await hass.async_block_till_done()
    assert hub.remotes["valen:13b3"].lamps == [l1]


async def test_pairing_long_after_restore_does_not_bind(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = await _lose_power(hass, hub, l1, fake_air, fake_clock, fake_board)
    fake_air.beacon(L1, ACTIVE, start + 20, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 20)
    fake_clock.now += 61
    fire_raw_adv(hass, remote_press("pairing", 0x13B3, 120), fake_board.device_id)
    await hass.async_block_till_done()
    assert "valen:13b3" not in hub.remotes


async def test_presence_timer_runs_in_the_event_loop(hass, fake_board, hass_storage, fake_air, fake_clock):
    """The real timer, not a direct call: lost and back must reach the entities on their own."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    power = "binary_sensor.street_power"
    fake_air.beacon(L1, ACTIVE, fake_clock.now, board=fake_board.device_id)
    fake_clock.now += 2
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(power).state == "on"
    fake_clock.now += 12
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(power).state == "off"
    assert hass.states.get("light.street").state == "unavailable"


async def test_quiet_beacon_after_missed_bursts_brings_lamp_back(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    """Bursts were missed, the lamp never lost power: available again, state untouched."""
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    start = fake_clock.now
    fake_air.beacon(L1, QUIET, start, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 2)
    hub.set_assumed_state(l1, LampState(True, 2700, 30))
    await _tick(hass, hub, fake_clock, start + 200)
    lamp = hub.lamps[l1]
    assert lamp.present is False
    fake_air.beacon(L1, QUIET, start + 240, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, start + 242)
    assert lamp.present is True
    assert lamp.state == LampState(True, 2700, 30)
    assert lamp.before_loss is None
    assert hass.states.get("light.street").state == "on"
    assert hass.states.get("binary_sensor.street_power").state == "on"


async def test_disconnected_board_is_not_the_transmitting_one(
    hass, make_board, hass_storage, fake_air, fake_clock
):
    """HA keeps an unplugged board's last RSSI for a while: it must not count."""
    street = make_board("ble-proxy-tx-street", "BLE Proxy TX (Street)")
    kitchen = make_board()
    _, hub, l1 = await _setup(hass, [street, kitchen], hass_storage)
    fake_air.beacon(L1, ACTIVE, fake_clock.now)
    fake_air.rssi[L1] = {street.device_id: -74, kitchen.device_id: -45}
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert hub.lamps[l1].board == kitchen.device_id
    kitchen.set_connected(False)
    await hass.async_block_till_done()
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    assert hub.lamps[l1].board == street.device_id
    assert hass.states.get("sensor.street_transmitting_proxy").state == "BLE Proxy TX (Street)"


async def _active_for(hass, hub, clock, air, board, seconds, step=0.5):
    """The lamp in active mode: a fresh advert at every tick for `seconds`."""
    end = clock.now + seconds
    while clock.now + step <= end + 1e-9:
        clock.now += step
        air.beacon(L1, ACTIVE, clock.now, board=board.device_id if board else None)
        hub.presence_tick()
    await hass.async_block_till_done()


async def _silent_for(hass, hub, clock, seconds, step=0.5):
    end = clock.now + seconds
    while clock.now + step <= end + 1e-9:
        clock.now += step
        hub.presence_tick()
    await hass.async_block_till_done()


async def test_quiet_to_active_is_a_power_cycle(hass, fake_board, hass_storage, fake_air, fake_clock):
    """Only mains power brings a lamp back to active mode: a quick switch cycle is certain."""
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_air.beacon(L1, QUIET, fake_clock.now, board=fake_board.device_id)
    await _tick(hass, hub, fake_clock, fake_clock.now + 2)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    await _silent_for(hass, hub, fake_clock, 4)  # off and on again at the wall, quickly
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    lamp = hub.lamps[l1]
    assert lamp.state == LampState(True, 3000, 100)
    assert lamp.uncertain is False
    assert lamp.present is True


async def test_short_silence_in_active_mode_marks_uncertain(
    hass, fake_board, hass_storage, fake_air, fake_clock
):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 2)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    await _silent_for(hass, hub, fake_clock, 3.5)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    lamp = hub.lamps[l1]
    assert lamp.uncertain is True
    assert lamp.state == LampState(False, 2700, 50)  # maybe switched at the wall, maybe not
    assert lamp.present is True


async def test_shorter_silence_is_ignored(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 2)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    await _silent_for(hass, hub, fake_clock, 2)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    assert hub.lamps[l1].uncertain is False


async def test_silence_while_sending_is_ignored(hass, fake_board, hass_storage, fake_air, fake_clock):
    """The board does not listen while it sends: that silence is ours."""
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 2)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _silent_for(hass, hub, fake_clock, 1)
    await hub.async_set_target(l1, Target(True, 2200, 5))  # a long chain
    await _silent_for(hass, hub, fake_clock, 2.5)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    assert hub.lamps[l1].uncertain is False


async def test_power_cycle_gap_is_configurable(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, options={CONF_POWER_CYCLE_GAP: 5.0})
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 2)
    hub.set_assumed_state(l1, LampState(False, 2700, 50))
    await _silent_for(hass, hub, fake_clock, 3.5)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    assert hub.lamps[l1].uncertain is False
    await _silent_for(hass, hub, fake_clock, 5.5)
    await _active_for(hass, hub, fake_clock, fake_air, fake_board, 1)
    assert hub.lamps[l1].uncertain is True


async def test_board_does_not_flap_between_two_boards_side_by_side(
    hass, make_board, hass_storage, fake_air, fake_clock
):
    """Two boards next to each other: their RSSI readings swing by more than the hysteresis,
    alternately in favour of each. The lamp must stay on one board."""
    street = make_board("ble-proxy-tx-street", "BLE Proxy TX (Street)")
    kitchen = make_board()
    _, hub, l1 = await _setup(hass, [street, kitchen], hass_storage)
    swings = [(-50, -62), (-63, -49), (-51, -60), (-61, -50)]
    boards_seen = set()
    for i in range(240):  # two minutes of ticks
        a, b = swings[i % len(swings)]
        fake_air.beacon(L1, ACTIVE, fake_clock.now + 0.5)
        fake_air.rssi[L1] = {street.device_id: a, kitchen.device_id: b}
        fake_clock.now += 0.5
        hub.presence_tick()
        if i > 4:
            boards_seen.add(hub.lamps[l1].board)
    assert len(boards_seen) == 1


async def test_board_switches_after_a_lasting_change(hass, make_board, hass_storage, fake_air, fake_clock):
    street = make_board("ble-proxy-tx-street", "BLE Proxy TX (Street)")
    kitchen = make_board()
    _, hub, l1 = await _setup(hass, [street, kitchen], hass_storage)
    fake_air.rssi[L1] = {street.device_id: -50, kitchen.device_id: -70}
    await _active_for(hass, hub, fake_clock, fake_air, None, 5)
    assert hub.lamps[l1].board == street.device_id
    fake_air.rssi[L1] = {street.device_id: -75, kitchen.device_id: -50}  # the lamp was moved, say
    await _active_for(hass, hub, fake_clock, fake_air, None, 10)
    assert hub.lamps[l1].board == street.device_id  # not within the 30 s dwell
    await _active_for(hass, hub, fake_clock, fake_air, None, 30)
    assert hub.lamps[l1].board == kitchen.device_id
    await hub.async_power(l1, True)
    assert (len(street.sent), len(kitchen.sent)) == (0, 1)
