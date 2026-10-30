"""The hub tracks remote presses and remotes."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.ble_mesh_lights.const import DOMAIN, SIGNAL_REMOTE, STORAGE_STATE
from custom_components.ble_mesh_lights.planner import Target
from custom_components.ble_mesh_lights.state import LampState

from .conftest import fire_raw_adv, lamp_subentry, remote_press, setup_entry, wait_for

R1 = "valen:37af"  # remote E8:26:CF:6B:37:AF
R2 = "valen:13b3"  # remote 18:90:67:6C:13:B3


async def _setup(hass, boards, hass_storage, **lamp):
    entry = await setup_entry(hass, boards, lamps=[lamp_subentry(**lamp)], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    return entry, entry.runtime_data, sub_id


def _record(hass, key):
    presses = []
    async_dispatcher_connect(hass, SIGNAL_REMOTE.format(key), presses.append)
    return presses


async def _press(hass, board, command, source, sequence):
    fire_raw_adv(hass, remote_press(command, source, sequence), board.device_id)
    await hass.async_block_till_done()


async def test_bound_remote_press_updates_lamp(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_bind_remote(R1, [l1])
    presses = _record(hass, R1)
    await _press(hass, fake_board, "off", 0x37AF, 2280)
    assert hub.lamps[l1].state.on is False
    assert [p.command for p in presses] == ["off"]


async def test_press_from_two_boards_applied_once(hass, make_board, hass_storage):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await hub.async_bind_remote(R1, [l1])
    presses = _record(hass, R1)
    raw = remote_press("brightness_down", 0x37AF, 2280)
    fire_raw_adv(hass, raw, kitchen.device_id)
    fire_raw_adv(hass, raw, corridor.device_id)
    await hass.async_block_till_done()
    assert hub.lamps[l1].state == LampState(True, 3000, 80)
    assert len(presses) == 1


async def test_missed_press_marks_uncertain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await hub.async_bind_remote(R1, [l1])
    await _press(hass, fake_board, "on", 0x37AF, 2280)
    await _press(hass, fake_board, "brightness_down", 0x37AF, 2310)
    assert hub.lamps[l1].state.level is None
    assert hub.lamps[l1].uncertain is True


async def test_unknown_remote_reported_once(hass, fake_board, hass_storage):
    _, hub, _ = await _setup(hass, [fake_board], hass_storage)
    await _press(hass, fake_board, "on", 0x13B3, 100)
    await _press(hass, fake_board, "off", 0x13B3, 110)
    assert R2 in hub.unknown_remotes
    issues = [i for (domain, i) in ir.async_get(hass).issues if domain == DOMAIN and "new_remote_" in i]
    assert issues == [f"new_remote_{R2}"]
    assert 0x13B3 in hub.remote_sources()


async def test_ignored_remote_not_reported(hass, fake_board, hass_storage):
    _, hub, _ = await _setup(hass, [fake_board], hass_storage)
    hub.async_ignore_remote(R2)
    await _press(hass, fake_board, "on", 0x13B3, 100)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None


async def test_own_address_ignored_and_collision_reported(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _press(hass, fake_board, "off", 0x7E01, 500)
    assert hub.lamps[l1].state.on is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, "address_collision_7e01") is not None
    assert hub.unknown_remotes == {}


async def test_own_send_heard_by_another_board_is_no_collision(hass, make_board, hass_storage):
    kitchen = make_board()
    corridor = make_board("ble-proxy-tx-corridor", "BLE Proxy TX (Corridor)")
    _, hub, l1 = await _setup(hass, [kitchen, corridor], hass_storage)
    await hub.async_power(l1, True)
    fire_raw_adv(hass, bytes.fromhex(kitchen.sent[0]["raw"]), corridor.device_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "address_collision_7e01") is None


async def test_wait_for_remote_press_skips_pairing_and_late(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    waiter = asyncio.ensure_future(hub.async_wait_for_remote_press(hub.lamps[l1].profile, timeout=5))
    await asyncio.sleep(0)
    await _press(hass, fake_board, "pairing", 0x13B3, 120)
    assert not waiter.done()
    await _press(hass, fake_board, "relax", 0x13B3, 130)
    assert await waiter == (R2, "relax")


async def test_press_taken_by_the_wizard_raises_no_repair(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    waiter = asyncio.ensure_future(hub.async_wait_for_remote_press(hub.lamps[l1].profile, timeout=5))
    await asyncio.sleep(0)
    await _press(hass, fake_board, "relax", 0x13B3, 130)
    assert await waiter == (R2, "relax")
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None


async def test_wait_for_remote_press_skips_a_late_press(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await _press(hass, fake_board, "on", 0x13B3, 130)
    waiter = asyncio.ensure_future(hub.async_wait_for_remote_press(hub.lamps[l1].profile, timeout=5))
    await asyncio.sleep(0)
    await _press(hass, fake_board, "off", 0x13B3, 120)  # older than the last one: late
    assert not waiter.done()
    await _press(hass, fake_board, "bright", 0x13B3, 140)
    assert await waiter == (R2, "bright")


async def test_wait_for_remote_press_times_out(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    with pytest.raises(TimeoutError):
        await hub.async_wait_for_remote_press(hub.lamps[l1].profile, timeout=0.01)


async def test_add_remote_lamp_keeps_other_lamps(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass,
        [fake_board],
        lamps=[
            lamp_subentry(),
            lamp_subentry(mac="E8:26:CF:00:00:02", address=0x7E02, index=2, title="Patio"),
        ],
        hass_storage=hass_storage,
    )
    hub = entry.runtime_data
    l1, l2 = entry.subentries
    await _press(hass, fake_board, "on", 0x37AF, 2280)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R1}") is not None
    await hub.async_add_remote_lamp(R1, l1)
    await hub.async_add_remote_lamp(R1, l2)
    await hub.async_add_remote_lamp(R1, l2)
    assert hub.remotes[R1].lamps == [l1, l2]
    assert hub.remote_lamp_names(R1) == ["Street", "Patio"]
    assert hub.remote_lamp_names(R2) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R1}") is None


async def test_tracker_baselines_survive_restart(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_bind_remote(R1, [l1])
    await _press(hass, fake_board, "on", 0x37AF, 2280)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    presses = _record(hass, R1)
    await _press(hass, fake_board, "off", 0x37AF, 2310)
    assert presses[0].missed == 2


async def test_remote_press_cancels_pending_chain(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await hub.async_bind_remote(R1, [l1])
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await wait_for(lambda: len(fake_board.sent) >= 1)
    fire_raw_adv(hass, remote_press("off", 0x37AF, 2280), fake_board.device_id)
    await chain
    await hass.async_block_till_done()
    assert len(fake_board.sent) <= 2
    assert hub.lamps[l1].state.on is False


async def test_repeat_scene_press_stops_chain(hass, fake_board, hass_storage):
    """Any press of a bound remote wins over a waiting chain, even one that leaves the state as it
    is (the lamp already in the pressed scene)."""
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    await hub.async_bind_remote(R1, [l1])
    lock = hub._lamp_locks.setdefault(l1, asyncio.Lock())
    await lock.acquire()
    chain = asyncio.ensure_future(hub.async_set_target(l1, Target(True, 2200, 5)))
    await asyncio.sleep(0)
    fire_raw_adv(hass, remote_press("relax", 0x37AF, 2280), fake_board.device_id)
    await hass.async_block_till_done()
    lock.release()
    await chain
    assert fake_board.sent == []


async def test_subentry_remote_binding_merged(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, remote=R1)
    assert hub.remotes[R1].lamps == [l1]
    assert hub.remotes[R1].source == 0x37AF


async def test_deleted_lamp_pruned_from_remotes(hass, fake_board, hass_storage):
    hass_storage[STORAGE_STATE] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_STATE,
        "data": {"lamps": {}, "remotes": {R1: {"profile": "valen", "source": 0x37AF, "lamps": ["gone"]}}},
    }
    _, hub, _ = await _setup(hass, [fake_board], hass_storage)
    assert hub.remotes[R1].lamps == []


async def test_bound_remote_survives_restart(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_bind_remote(R1, [l1])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.remotes[R1].lamps == [l1]


async def test_wizard_remote_heard_before_the_lamp_exists_gets_no_issue(hass, fake_board, hass_storage):
    """Pressed between pairing and "Finish": unknown for a moment, then bound by the new lamp."""
    from homeassistant.config_entries import ConfigSubentry

    from .conftest import seed_counters

    entry = await setup_entry(hass, [fake_board], hass_storage=hass_storage)
    await _press(hass, fake_board, "off", 0x37AF, 2280)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R1}") is not None
    seed_counters(hass_storage, {0x7E01: 0})
    hass.config_entries.async_add_subentry(entry, ConfigSubentry(**lamp_subentry(remote=R1)))
    await hass.async_block_till_done()
    assert R1 in entry.runtime_data.remotes
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R1}") is None


async def test_unignored_remote_is_reported_again(hass, fake_board, hass_storage):
    _, hub, _ = await _setup(hass, [fake_board], hass_storage)
    await _press(hass, fake_board, "on", 0x13B3, 100)
    hub.async_ignore_remote(R2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None
    hub.async_ignore_remote(R2, ignore=False)
    await _press(hass, fake_board, "off", 0x13B3, 110)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is not None
