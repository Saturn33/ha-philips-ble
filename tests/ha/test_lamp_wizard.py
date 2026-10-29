"""The lamp wizard: detect the lamp by a power cycle, pair, bind a remote, test."""

from __future__ import annotations

import asyncio

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


async def _to_saved(hass, entry, board, fake_air, fake_clock, no_sleep, name="Street"):
    """Through pairing and the test to the save: the lamp exists, the wizard offers a remote."""
    flow_id = await _start(hass, entry, name=name)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=board.device_id)
    await _choose(hass, flow_id, "test")
    assert await _step(hass, flow_id) == "test_result"
    await _choose(hass, flow_id, "save")
    assert await _step(hass, flow_id) == "remote"
    (sub_id,) = [sub_id for sub_id, sub in entry.subentries.items() if sub.title == name]
    return flow_id, sub_id


async def _to_heard(hass, flow_id, board, command="relax", source=0x37AF, sequence=2280):
    await _choose(hass, flow_id, "remote_pair")
    assert await _step(hass, flow_id) == "remote_pair"
    await hass.config_entries.subentries.async_configure(flow_id, {})
    assert await _step(hass, flow_id, wait=False) == "remote_press"
    await _press(hass, board, command, source, sequence)
    assert await _step(hass, flow_id) == "remote_heard"
    return _flow(hass, flow_id).cur_step


async def test_full_wizard_creates_lamp(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    from custom_components.ble_mesh_lights.state import LampState

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    assert await _step(hass, flow_id) == "blinked"
    sent = [decode_sent(call) for call in fake_board.sent]
    assert [name for _, _, name in sent] == ["pairing"] * 20
    assert {source for source, _, _ in sent} == {0x7E01}
    assert {call["repeat"] for call in fake_board.sent} == {1.0}
    assert entry.data[DATA_USED_INDICES] == [1]

    await _choose(hass, flow_id, "test")
    assert await _step(hass, flow_id) == "test_result"
    assert _sent_names(fake_board)[-2:] == ["off", "on"]
    await _choose(hass, flow_id, "save")
    assert await _step(hass, flow_id) == "remote"
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Street"
    assert subentry.unique_id == L1
    assert subentry.data["address"] == 0x7E01 and subentry.data["index"] == 1
    assert subentry.data["mac"] == L1 and subentry.data["profile"] == "valen"
    assert subentry.data["after_outage"] == "nothing"
    assert subentry.data["aliases"] == ["свет на улице"]
    assert subentry.data["remote"] is None
    hub = entry.runtime_data
    assert hub.lamps[subentry.subentry_id].state == LampState(True, 3000, 100)

    step = await _to_heard(hass, flow_id, fake_board)
    assert step["description_placeholders"]["address"] == "0x37AF"
    assert step["description_placeholders"]["button"] == "Relax"
    assert step["description_placeholders"]["lamps"] == "—"
    result = await _choose(hass, flow_id, "remote_bind")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "remote_bound"
    hub = entry.runtime_data
    assert hub.remotes["valen:37af"].lamps == [subentry.subentry_id]
    assert hub.lamps[subentry.subentry_id].state == LampState(True, 2700, 50, "relax")


async def test_lamp_saved_before_the_remote_step(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    """The dialog is closed at "Bind a remote?": the lamp stays and works."""
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, sub_id = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    hass.config_entries.subentries.async_abort(flow_id)
    await hass.async_block_till_done()
    sent = len(fake_board.sent)
    await entry.runtime_data.async_power(sub_id, False)
    assert len(fake_board.sent) > sent
    assert decode_sent(fake_board.sent[-1])[0] == 0x7E01


async def test_wizard_done_without_remote(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, _ = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    result = await _choose(hass, flow_id, "done")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "lamp_added"
    assert entry.runtime_data.remotes == {}


async def test_non_scene_press_binds_without_state_change(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    from custom_components.ble_mesh_lights.state import LampState

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, sub_id = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    step = await _to_heard(hass, flow_id, fake_board, command="off")
    assert step["description_placeholders"]["button"] == "Off"
    await _choose(hass, flow_id, "remote_bind")
    hub = entry.runtime_data
    assert hub.remotes["valen:37af"].lamps == [sub_id]
    assert hub.lamps[sub_id].state == LampState(True, 3000, 100)


async def test_remote_not_heard_offers_retry(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    from custom_components.ble_mesh_lights import config_flow

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, _ = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    config_flow.REMOTE_BUTTON_TIMEOUT, old = 0.01, config_flow.REMOTE_BUTTON_TIMEOUT
    try:
        await _choose(hass, flow_id, "remote_pair")
        await hass.config_entries.subentries.async_configure(flow_id, {})
        await asyncio.sleep(0.05)
        assert await _step(hass, flow_id) == "remote_not_heard"
    finally:
        config_flow.REMOTE_BUTTON_TIMEOUT = old
    await _choose(hass, flow_id, "remote_pair")
    assert await _step(hass, flow_id) == "remote_pair"


async def test_wrong_remote_try_again(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, sub_id = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    step = await _to_heard(hass, flow_id, fake_board, source=0x13B3, sequence=120)
    assert step["description_placeholders"]["address"] == "0x13B3"
    await _choose(hass, flow_id, "remote_press", wait=False)
    assert await _step(hass, flow_id, wait=False) == "remote_press"
    await _press(hass, fake_board, "bright", 0x37AF, 2280)
    step = _flow(hass, flow_id).cur_step
    assert await _step(hass, flow_id) == "remote_heard"
    step = _flow(hass, flow_id).cur_step
    assert step["description_placeholders"]["address"] == "0x37AF"
    await _choose(hass, flow_id, "remote_bind")
    hub = entry.runtime_data
    assert hub.remotes["valen:37af"].lamps == [sub_id]
    assert "valen:13b3" not in hub.remotes


async def test_remote_press_after_the_save_reload(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    """Saving the lamp reloads the entry: the press screen listens on the new hub."""
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    old_hub = entry.runtime_data
    flow_id, sub_id = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    assert entry.runtime_data is not old_hub and old_hub.closed
    await _to_heard(hass, flow_id, fake_board)
    await _choose(hass, flow_id, "remote_bind")
    assert entry.runtime_data.remotes["valen:37af"].lamps == [sub_id]


async def test_remote_already_bound_by_its_pairing(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    """The user pairs the remote after a power cycle: the hub binds it by its pairing request
    already; the heard screen says so and Bind changes nothing else."""
    from custom_components.ble_mesh_lights import hub as hub_module

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, sub_id = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    hub = entry.runtime_data
    hub._restored_at[sub_id] = hub_module.monotonic()  # the lamp just got power
    await _choose(hass, flow_id, "remote_pair")
    await _press(hass, fake_board, "pairing", 0x37AF, 2270)
    assert hub.remotes["valen:37af"].lamps == [sub_id]
    await hass.config_entries.subentries.async_configure(flow_id, {})
    assert await _step(hass, flow_id, wait=False) == "remote_press"
    await _press(hass, fake_board, "relax", 0x37AF, 2280)
    assert await _step(hass, flow_id) == "remote_heard"
    assert _flow(hass, flow_id).cur_step["description_placeholders"]["lamps"] == "Street"
    await _choose(hass, flow_id, "remote_bind")
    assert entry.runtime_data.remotes["valen:37af"].lamps == [sub_id]


def test_remote_text_has_the_hint():
    import json

    import yaml

    from .conftest import ROOT

    package = ROOT / "custom_components/ble_mesh_lights"
    ru = json.loads((package / "translations/ru.json").read_text(encoding="utf-8"))
    en = json.loads((package / "strings.json").read_text(encoding="utf-8"))
    for texts in (en, ru):
        assert "{remote_hint}" in texts["config_subentries"]["lamp"]["step"]["remote_pair"]["description"]
    valen = yaml.safe_load((package / "profiles/valen/profile.yaml").read_text(encoding="utf-8"))
    assert "«Вкл»" in valen["pairing"]["remote_hint"]["ru"]
    assert "любую кнопку" in valen["pairing"]["remote_hint"]["ru"]


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


async def test_wait_for_known_lamp_return(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    """Pair again waits for this configured lamp; adding a lamp still skips it."""
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    hub = entry.runtime_data
    profile = hub.profiles["valen"]
    fake_air.beacon(L1, beacon(L1), fake_clock.now, board=fake_board.device_id)
    waiter = asyncio.ensure_future(hub.async_wait_for_new_lamp(profile, 120, mac=L1))
    polls = len(no_sleep)
    await wait_for(lambda: len(no_sleep) > polls)
    fake_air.beacon(L1, beacon(L1), fake_clock.now + 5, board=fake_board.device_id)  # after a silence
    assert await waiter == L1


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
    await _choose(hass, flow_id, "test")
    await _choose(hass, flow_id, "test_failed")
    assert await _step(hass, flow_id) == "test_failed"
    result = await _choose(hass, flow_id, "reset_abort")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reset_needed"
    assert "five times" in result["description_placeholders"]["reset_hint"]


async def test_reset_hint_in_the_ha_language(hass, fake_board, hass_storage):
    hass.config.language = "ru"
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    assert "Пять раз" in result["description_placeholders"]["reset_hint"]


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
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "settings"})
    assert result["step_id"] == "settings"
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
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "settings"})
    assert "after_outage" not in result["data_schema"].schema


async def _pair_again(hass, entry, sub_id, board, fake_air, fake_clock, no_sleep):
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_LAMP), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    flow_id = result["flow_id"]
    result = await flows.async_configure(flow_id, {"next_step_id": "pair_again"})
    assert result["step_id"] == "pair_again"
    assert "five times" in result["description_placeholders"]["reset_hint"]
    fake_air.beacon(L1, beacon(L1), fake_clock.now, board=board.device_id)  # on before the cycle
    result = await flows.async_configure(flow_id, {})
    assert result["step_id"] == "power_cycle"
    polls = len(no_sleep)
    await wait_for(lambda: len(no_sleep) > polls)
    fake_air.beacon(L1, beacon(L1), fake_clock.now + 20, board=board.device_id)  # back after 20 s
    await hass.async_block_till_done()
    # No "Did it blink?": a light that still knows the address does not blink. Straight to the test.
    assert await _step(hass, flow_id) == "test_result"
    assert _sent_names(board)[-22:] == ["pairing"] * 20 + ["off", "on"]
    await _choose(hass, flow_id, "save")
    assert await _step(hass, flow_id) == "remote"
    return flow_id


async def test_pair_again_keeps_address_and_bindings(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    await entry.runtime_data.async_bind_remote("valen:37af", [sub_id])
    used = list(entry.data[DATA_USED_INDICES])
    flow_id = await _pair_again(hass, entry, sub_id, fake_board, fake_air, fake_clock, no_sleep)
    pairing = [decode_sent(call) for call in fake_board.sent if decode_sent(call)[2] == "pairing"]
    assert len(pairing) == 20 and {source for source, _, _ in pairing} == {0x7E01}
    assert entry.data[DATA_USED_INDICES] == used
    result = await _choose(hass, flow_id, "done")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "paired_again"
    assert list(entry.subentries) == [sub_id]
    assert entry.subentries[sub_id].data["address"] == 0x7E01
    assert entry.runtime_data.remotes["valen:37af"].lamps == [sub_id]


async def test_pair_again_then_bind_remote(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    await entry.runtime_data.async_bind_remote("valen:37af", [sub_id])
    flow_id = await _pair_again(hass, entry, sub_id, fake_board, fake_air, fake_clock, no_sleep)
    await _to_heard(hass, flow_id, fake_board, source=0x09FC, sequence=500)
    result = await _choose(hass, flow_id, "remote_bind")
    assert result["reason"] == "remote_bound"
    hub = entry.runtime_data
    assert hub.remotes["valen:09fc"].lamps == [sub_id]
    assert hub.remotes["valen:37af"].lamps == [sub_id]


async def test_reload_during_pairing_keeps_counters(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep, monkeypatch
):
    """The entry reloads while the wizard waits for the power cycle, and the new hub moves lamp B's
    counter on: the wizard must pair through the new hub, and no counter on disk may go back."""
    from custom_components.ble_mesh_lights import config_flow
    from custom_components.ble_mesh_lights.const import STORAGE_SEQUENCE

    # The fake clock moves on with every poll while the reload runs: give the search time.
    monkeypatch.setattr(config_flow, "NEW_LAMP_TIMEOUT", 100_000.0)
    monkeypatch.setattr(config_flow, "RELOAD_WAIT", 100_000.0)

    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage, counters={0x7E01: 300}
    )
    (lamp_b,) = entry.subentries
    flow_id = await _start(hass, entry, name="Hall")
    await wait_for(lambda: len(no_sleep) > 0)
    old_hub = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)  # e.g. options saved
    assert entry.runtime_data is not old_hub
    await entry.runtime_data.async_power(lamp_b, True)  # the new hub reserves B's next block

    def stored():
        return {k: v for k, v in hass_storage[STORAGE_SEQUENCE]["data"]["addresses"].items()}

    highest = stored()
    assert highest["0x7e01"] >= 556
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, mac=L2, board=fake_board.device_id)
    for key, value in stored().items():
        assert value >= highest.get(key, 0), key
    pairing = [decode_sent(call) for call in fake_board.sent if decode_sent(call)[2] == "pairing"]
    assert len(pairing) == 20 and {source for source, _, _ in pairing} == {0x7E02}
    assert await _step(hass, flow_id) == "blinked"


async def _radio_tail_cycle(hass, fake_air, fake_clock, no_sleep, board, off_seconds):
    """A lamp already on is switched off for `off_seconds`: its radio goes on for 7 s on the
    power supply's capacitor, so the air is silent only for off_seconds - 7 (if at all)."""
    polls = len(no_sleep)
    await wait_for(lambda: len(no_sleep) > polls)
    start = fake_clock.now
    timeline = [start + 0.5 * i for i in range(int((off_seconds + 3) / 0.5))]
    for t in timeline:
        while fake_clock.now < t:
            await asyncio.sleep(0)
        on_air = t - start < 7 or t - start >= off_seconds
        if on_air:
            fake_air.beacon(L2, beacon(L2), t, board=board.device_id)
    await hass.async_block_till_done()


async def test_power_cycle_detected_with_the_radio_tail(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    fake_air.beacon(L2, beacon(L2), fake_clock.now, board=fake_board.device_id)  # the lamp is on
    flow_id = await _start(hass, entry)
    await _radio_tail_cycle(hass, fake_air, fake_clock, no_sleep, fake_board, off_seconds=11)
    assert await _step(hass, flow_id) == "blinked"


async def test_five_second_cycle_is_not_seen(hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep):
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    fake_air.beacon(L2, beacon(L2), fake_clock.now, board=fake_board.device_id)
    flow_id = await _start(hass, entry)
    await _radio_tail_cycle(hass, fake_air, fake_clock, no_sleep, fake_board, off_seconds=5)
    assert await _step(hass, flow_id) == "power_cycle_timeout"


def test_wizard_text_asks_for_about_fifteen_seconds():
    """The radio goes on for about 7 s after the switch-off, and the wizard needs 3 s of silence."""
    import json

    from .conftest import ROOT

    package = ROOT / "custom_components/ble_mesh_lights"
    en = json.loads((package / "strings.json").read_text(encoding="utf-8"))["config_subentries"]["lamp"]
    ru = json.loads((package / "translations/ru.json").read_text(encoding="utf-8"))["config_subentries"][
        "lamp"
    ]
    assert "about 15 seconds" in en["progress"]["power_cycle"]
    assert "about 15 seconds" in en["step"]["power_cycle_timeout"]["description"]
    assert "около 15 секунд" in ru["progress"]["power_cycle"]
    assert "около 15 секунд" in ru["step"]["power_cycle_timeout"]["description"]


def test_board_sensor_disabled_text_says_reload():
    import json

    from .conftest import ROOT

    package = ROOT / "custom_components/ble_mesh_lights"
    en = json.loads((package / "strings.json").read_text(encoding="utf-8"))
    ru = json.loads((package / "translations/ru.json").read_text(encoding="utf-8"))
    assert "reload BLE Mesh Lights" in en["issues"]["board_sensor_disabled"]["description"]
    assert "перезагрузите BLE Mesh Lights" in ru["issues"]["board_sensor_disabled"]["description"]


async def test_entry_gone_while_waiting_for_the_remote_aborts(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    """The entry is unloaded (not reloaded) while the wizard waits for the remote: after the wait
    for the entry it aborts with a reason, not an unknown error."""
    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    flow_id, _ = await _to_saved(hass, entry, fake_board, fake_air, fake_clock, no_sleep)
    await _choose(hass, flow_id, "remote_pair")
    await hass.config_entries.subentries.async_configure(flow_id, {})
    assert await _step(hass, flow_id, wait=False) == "remote_press"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_sequence_error_is_not_shown_as_no_address(
    hass, fake_board, hass_storage, fake_air, fake_clock, no_sleep
):
    from custom_components.ble_mesh_lights.sequence import SequenceError

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)

    async def broken(address):
        raise SequenceError("Sequence space of 0x7e01 is exhausted; use another address")

    entry.runtime_data.counters.async_next = broken
    flow_id = await _start(hass, entry)
    await _power_cycle(hass, fake_air, fake_clock, no_sleep, board=fake_board.device_id)
    result = await hass.config_entries.subentries.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "send_failed"
    assert "exhausted" in result["description_placeholders"]["error"]


def test_board_not_ready_text_names_the_heartbeat():
    import json

    from .conftest import ROOT

    package = ROOT / "custom_components/ble_mesh_lights"
    en = json.loads((package / "strings.json").read_text(encoding="utf-8"))
    ru = json.loads((package / "translations/ru.json").read_text(encoding="utf-8"))
    assert "esphome.ble_adv_tx.heartbeat" in en["issues"]["board_not_ready"]["description"]
    assert "esphome.ble_adv_tx.heartbeat" in ru["issues"]["board_not_ready"]["description"]


def test_outage_sensor_text_has_no_repo_path():
    from .conftest import ROOT

    package = ROOT / "custom_components/ble_mesh_lights"
    for path in (
        package / "strings.json",
        package / "translations/en.json",
        package / "translations/ru.json",
    ):
        assert "docs/KNOWN_ISSUES.md" not in path.read_text(encoding="utf-8")
