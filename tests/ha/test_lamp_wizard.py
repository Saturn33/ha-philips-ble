"""The lamp wizard: detect the lamp by a power cycle, pair, bind a remote, test."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ble_mesh_lights.const import CONF_OUTAGE_SENSOR, DATA_USED_INDICES, SUBENTRY_LAMP

from .conftest import L1, decode_sent, fire_raw_adv, lamp_subentry, remote_press, setup_entry, wait_for

L2 = "E8:26:CF:00:00:02"  # made-up second lamp


def beacon(mac: str, quiet: bool = False) -> bytes:
    reversed_mac = bytes.fromhex(mac.replace(":", ""))[::-1].hex()
    return bytes.fromhex(f"182b0000ffffffff3101{reversed_mac}{'01' if quiet else '00'}0001000000000000")


def _sent_names(board):
    return [decode_sent(call)[2] for call in board.sent]


async def _start(hass, entry, name="Street", aliases=("свет на улице",)):
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await flows.async_configure(
        result["flow_id"], {"profile": "valen", "name": name, "aliases": list(aliases)}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS and result["step_id"] == "power_cycle"
    return result["flow_id"]


async def _power_cycle(hass, fake_air, fake_clock, no_sleep, mac=L1, board=None, rssi=-50):
    """The lamp comes back after the wizard started looking."""
    polls = len(no_sleep)
    await wait_for(lambda: len(no_sleep) > polls)
    fake_air.beacon(mac, beacon(mac), fake_clock.now, rssi=rssi, board=board)
    await hass.async_block_till_done()


def _flow(hass, flow_id):
    return hass.config_entries.subentries._progress[flow_id]


async def _settle(hass, flow_id) -> None:
    """After a progress task ends, the frontend calls configure once more to get the next step."""
    while _flow(hass, flow_id).cur_step["type"] is FlowResultType.SHOW_PROGRESS_DONE:
        await hass.config_entries.subentries.async_configure(flow_id)


async def _step(hass, flow_id, wait: bool = True):
    """The step the user sees now. With wait, short progress tasks (pairing burst, test) run to
    the end first; steps waiting for the user (remote) must be checked without it."""
    await _settle(hass, flow_id)
    if wait:
        await hass.async_block_till_done()
        await _settle(hass, flow_id)
    return _flow(hass, flow_id).cur_step["step_id"]


async def _choose(hass, flow_id, option, wait: bool = True):
    await _settle(hass, flow_id)
    result = await hass.config_entries.subentries.async_configure(flow_id, {"next_step_id": option})
    if wait:
        await hass.async_block_till_done()
    return result


async def _press(hass, board, command, source, sequence):
    fire_raw_adv(hass, remote_press(command, source, sequence), board.device_id)
    await hass.async_block_till_done()


async def test_full_wizard_creates_lamp(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert await _step(hass, flow_id) == "blinked"
    sent = [decode_sent(call) for call in fake_board.sent]
    assert [name for _, _, name in sent] == ["pairing"] * 20
    assert {source for source, _, _ in sent} == {0x7E01}
    assert {call["repeat"] for call in fake_board.sent} == {1.0}
    assert entry.data[DATA_USED_INDICES] == [1]

    await _choose(hass, flow_id, "remote")
    assert await _step(hass, flow_id) == "remote"
    await _choose(hass, flow_id, "remote_wait", wait=False)
    assert await _step(hass, flow_id, wait=False) == "remote_wait"
    await _press(hass, fake_board, "pairing", 0x37AF, 2270)
    assert await _step(hass, flow_id, wait=False) == "remote_button"
    await _press(hass, fake_board, "on", 0x37AF, 2280)
    assert await _step(hass, flow_id) == "test_result"
    assert _sent_names(fake_board)[-2:] == ["off", "on"]

    await _step(hass, flow_id)
    result = await _choose(hass, flow_id, "finish")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Street"
    assert subentry.unique_id == L1
    assert subentry.data["address"] == 0x7E01 and subentry.data["index"] == 1
    assert subentry.data["mac"] == L1 and subentry.data["profile"] == "valen"
    assert subentry.data["after_outage"] == "nothing"
    assert subentry.data["aliases"] == ["свет на улице"]
    assert subentry.data["remote"] == "valen:37af"
    hub = entry.runtime_data
    assert hub.remotes["valen:37af"].lamps == [subentry.subentry_id]
    lamp = hub.lamps[subentry.subentry_id]
    assert (lamp.state.on, lamp.state.color_temp, lamp.state.level) == (True, 3000, 100)


async def test_wizard_without_remote(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    await _choose(hass, flow_id, "remote")
    await _choose(hass, flow_id, "test")
    assert await _step(hass, flow_id) == "test_result"
    await _step(hass, flow_id)
    result = await _choose(hass, flow_id, "finish")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.data["remote"] is None


async def test_power_cycle_timeout_offers_retry(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await hass.async_block_till_done()  # nothing appears within 120 s
    assert await _step(hass, flow_id) == "power_cycle_timeout"
    result = await hass.config_entries.subentries.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS and result["step_id"] == "power_cycle"
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert await _step(hass, flow_id) == "blinked"


async def test_not_blinked_repeats_with_same_address(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    fake_clock.now += 10
    await _choose(hass, flow_id, "power_cycle", wait=False)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert await _step(hass, flow_id) == "blinked"
    assert {decode_sent(call)[0] for call in fake_board.sent} == {0x7E01}
    assert entry.data[DATA_USED_INDICES] == [1]


async def test_abandoned_wizard_does_not_reuse_address(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    hass.config_entries.subentries.async_abort(flow_id)
    assert entry.data[DATA_USED_INDICES] == [1]
    fake_board.sent.clear()
    fake_clock.now += 10
    await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert {decode_sent(call)[0] for call in fake_board.sent} == {0x7E02}
    assert entry.data[DATA_USED_INDICES] == [1, 2]


async def test_counter_history_counts_as_used(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    """The integration was deleted and added again: its old addresses still have counters."""
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    await entry.runtime_data.counters.async_skip(0x7E01, 10)
    await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert {decode_sent(call)[0] for call in fake_board.sent} == {0x7E02}


async def test_known_lamp_not_detected_again(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    flow_id = await _start(hass, entry, name="Hall")
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, mac=L1, board=fake_board.device_id)
    assert await _step(hass, flow_id) == "power_cycle_timeout"


async def test_quiet_beacon_not_detected(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    polls = len(no_sleep)
    await wait_for(lambda: len(no_sleep) > polls)
    fake_air.beacon(L2, beacon(L2, quiet=True), fake_clock.now, board=fake_board.device_id)
    await hass.async_block_till_done()
    assert await _step(hass, flow_id) == "power_cycle_timeout"


async def test_wizard_pairs_through_best_board(
    hass, make_board, hass_storage, fake_air, fake_clock, no_sleep
):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    entry = await setup_entry(hass, [kitchen, corridor], hass_storage=hass_storage)
    await _start(hass, entry)
    fake_air.rssi[L2] = {kitchen.device_id: -80}
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, mac=L2, board=corridor.device_id, rssi=-45)
    assert (len(kitchen.sent), len(corridor.sent)) == (0, 20)


async def test_test_failed_offers_reset_hint(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    await _choose(hass, flow_id, "remote")
    await _choose(hass, flow_id, "test")
    await _choose(hass, flow_id, "test_failed")
    assert await _step(hass, flow_id) == "test_failed"
    result = await _choose(hass, flow_id, "reset_abort")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reset_needed"
    assert "five times" in result["description_placeholders"]["reset_hint"]


async def test_wizard_aborts_when_not_loaded(hass, fake_board, hass_storage):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    assert await hass.config_entries.async_unload(entry.entry_id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_loaded"


async def test_reconfigure_after_outage_option(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass,
        [fake_board],
        lamps=[lamp_subentry()],
        hass_storage=hass_storage,
        options={CONF_OUTAGE_SENSOR: "binary_sensor.power_outage"},
    )
    (sub_id,) = entry.subentries
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    assert result["step_id"] == "reconfigure"
    assert "five times" in result["description_placeholders"]["reset_hint"]
    result = await flows.async_configure(result["flow_id"], {"name": "Terrace", "after_outage": "restore"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    subentry = entry.subentries[sub_id]
    assert subentry.title == "Terrace"
    assert subentry.data["after_outage"] == "restore"
    assert entry.runtime_data.lamps[sub_id].after_outage == "restore"


async def test_reconfigure_without_outage_sensor_hides_option(hass, fake_board, hass_storage):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    assert "after_outage" not in result["data_schema"].schema
