"""Actions, repairs and diagnostics."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ble_mesh_lights.const import DOMAIN
from custom_components.ble_mesh_lights.diagnostics import async_get_config_entry_diagnostics
from custom_components.ble_mesh_lights.state import LampState

from .conftest import L1, decode_sent, lamp_subentry, setup_entry

LIGHT = "light.street"


async def _setup(hass, boards, hass_storage, **kwargs):
    entry = await setup_entry(hass, boards, lamps=[lamp_subentry()], hass_storage=hass_storage, **kwargs)
    (sub_id,) = entry.subentries
    return entry, entry.runtime_data, sub_id


async def test_set_assumed_state_sends_nothing(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.lamps[l1].uncertain = True
    await hass.services.async_call(
        DOMAIN,
        "set_assumed_state",
        {ATTR_ENTITY_ID: LIGHT, "on": True, "color_temp_kelvin": 3300, "brightness": 150},
        blocking=True,
    )
    assert fake_board.sent == []
    assert hub.lamps[l1].state == LampState(True, 3000, 50)
    assert hub.lamps[l1].uncertain is False


async def test_set_assumed_state_keeps_unspecified_values(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 80))
    await hass.services.async_call(
        DOMAIN, "set_assumed_state", {ATTR_ENTITY_ID: LIGHT, "on": False}, blocking=True
    )
    assert hub.lamps[l1].state == LampState(False, 2700, 80)


async def test_send_command(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage)
    await hass.services.async_call(
        DOMAIN, "send_command", {ATTR_ENTITY_ID: LIGHT, "command": "nightlight"}, blocking=True
    )
    assert [decode_sent(call)[2] for call in fake_board.sent] == ["nightlight"]


async def test_send_command_queue_response(hass, fake_board, hass_storage):
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
    lights = [LIGHT, "light.patio"]
    answer = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {ATTR_ENTITY_ID: lights, "command": "on", "queue": True},
        blocking=True,
        return_response=True,
    )
    assert answer == {LIGHT: {"sent": True}, "light.patio": {"sent": True}}
    assert sorted(decode_sent(call)[0] for call in fake_board.sent) == [0x7E01, 0x7E02]

    # One lamp's queue is full: that lamp says so, the other still gets its command.
    hub._presses[l2] = 10
    fake_board.sent.clear()
    answer = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {ATTR_ENTITY_ID: lights, "command": "off", "queue": True},
        blocking=True,
        return_response=True,
    )
    assert answer[LIGHT] == {"sent": True}
    assert answer["light.patio"]["sent"] is False and answer["light.patio"]["reason"] == "queue_full"
    assert [decode_sent(call)[0] for call in fake_board.sent] == [0x7E01]
    hub._presses[l2] = 0

    # An unavailable lamp is left out by HA; the other answers.
    hub.lamps[l2].present = False
    hub.lamp_changed(l2)
    await hass.async_block_till_done()
    answer = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {ATTR_ENTITY_ID: lights, "command": "on", "queue": True},
        blocking=True,
        return_response=True,
    )
    assert answer == {LIGHT: {"sent": True}}

    # All unavailable: HA refuses a call that asks for a response and matches nothing.
    hub.lamps[l1].present = False
    hub.lamp_changed(l1)
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {ATTR_ENTITY_ID: lights, "command": "on", "queue": True},
            blocking=True,
            return_response=True,
        )


async def test_send_command_queue_answers_reloaded(hass, fake_board, hass_storage):
    """A press still waiting when the entry unloads answers reason "reloaded", it does not raise."""
    import asyncio

    entry, _, _ = await _setup(hass, [fake_board], hass_storage)
    fake_board.hold = asyncio.Event()

    def press():
        return asyncio.ensure_future(
            hass.services.async_call(
                DOMAIN,
                "send_command",
                {ATTR_ENTITY_ID: LIGHT, "command": "on", "queue": True},
                blocking=True,
                return_response=True,
            )
        )

    first, second = press(), press()
    for _ in range(5):
        await asyncio.sleep(0)
    unload = asyncio.ensure_future(hass.config_entries.async_unload(entry.entry_id))
    await asyncio.sleep(0)
    fake_board.hold.set()
    await first
    assert (await second)[LIGHT] == {"sent": False, "reason": "reloaded", "error": "BLE Mesh Lights reloaded"}
    assert await unload


async def test_send_command_queue_rejects_unknown(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {ATTR_ENTITY_ID: LIGHT, "command": "pairing", "queue": True},
            blocking=True,
            return_response=True,
        )


@pytest.mark.parametrize("command", ["pairing", "no_such_command"])
async def test_send_command_rejects_pairing_and_unknown(hass, fake_board, hass_storage, command):
    await _setup(hass, [fake_board], hass_storage)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "send_command", {ATTR_ENTITY_ID: LIGHT, "command": command}, blocking=True
        )
    assert fake_board.sent == []


async def test_skip_sequence(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hass.services.async_call(DOMAIN, "skip_sequence", {ATTR_ENTITY_ID: LIGHT}, blocking=True)
    await hub.async_power(l1, True)
    assert decode_sent(fake_board.sent[0])[1] == 0x100000


async def test_outdated_board_issue(hass, make_board, hass_storage):
    board = make_board(version="0")
    await _setup(hass, [board], hass_storage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"board_outdated_{board.device_id}") is not None
    board.set_connected(False)
    await hass.async_block_till_done()
    board.set_connected(True, version="2")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"board_outdated_{board.device_id}") is None


async def test_board_not_ready_issue(hass, fake_board, hass_storage):
    fake_board.set_connected(False)
    await _setup(hass, [fake_board], hass_storage)
    issue_id = f"board_not_ready_{fake_board.device_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    fake_board.set_connected(True)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_sequence_store_fix_flow(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage, counters={}
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, context={"issue_id": "sequence_store_missing"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    (sub_id,) = entry.subentries
    await entry.runtime_data.async_power(sub_id, True)
    assert decode_sent(fake_board.sent[0])[1] == 0x800000


async def test_diagnostics(hass, fake_board, hass_storage):
    entry, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    await hub.async_power(l1, False)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["options"]["base_address"] == 0x7E00
    assert data["boards"] == [
        {"name": "BLE Proxy TX (Kitchen)", "node": "ble-proxy-tx-kitchen", "ready": True}
    ]
    (lamp,) = data["lamps"]
    assert lamp["mac"] == L1 and lamp["address"] == "0x7E01" and lamp["profile"] == "valen"
    assert lamp["state"] == {"on": False, "color_temp": 2700, "level": 50, "scene": None}
    assert data["counters"]["0x7e01"] >= 1
    assert data["remotes"] == {} and data["unknown_remotes"] == []
    await hub.async_power(l1, True)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["lamps"][0]["state"]["scene"] == "relax"  # 2700 K / 50 %, found from the parameters


async def test_disabled_package_sensor_raises_its_own_issue(hass, fake_board, hass_storage):
    """The sensor is the board's version and heartbeat: disabled, the board cannot be used, and the
    repair must say so instead of "firmware outdated"."""
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_update_entity(
        fake_board.version_entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    hass.states.async_remove(fake_board.version_entity_id)
    await _setup(hass, [fake_board], hass_storage)
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"board_sensor_disabled_{fake_board.device_id}") is not None
    assert issues.async_get_issue(DOMAIN, f"board_outdated_{fake_board.device_id}") is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"board_not_ready_{fake_board.device_id}") is None


async def test_diagnostics_of_an_entry_in_setup_error(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage, counters={}
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["state"] == "setup_error"
    assert data["options"]["base_address"] == 0x7E00


async def test_remove_device_of_unloaded_entry_is_refused(hass, fake_board, hass_storage):
    from homeassistant.helpers import device_registry as dr

    from custom_components.ble_mesh_lights import async_remove_config_entry_device

    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry(remote="valen:37af")], hass_storage=hass_storage
    )
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "remote_valen:37af"), entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await async_remove_config_entry_device(hass, entry, device) is False


async def test_unknown_package_state_raises_no_repair(hass, fake_board, hass_storage):
    """Right after a board boots HA may connect before the sensor's first publish: the state is
    `unknown` for a few seconds. That is "not reported yet", not an old package."""
    hass.states.async_set(fake_board.version_entity_id, "unavailable")
    await _setup(hass, [fake_board], hass_storage)
    hass.states.async_set(fake_board.version_entity_id, "unknown")
    await hass.async_block_till_done()
    hass.states.async_set(fake_board.version_entity_id, "2")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"board_outdated_{fake_board.device_id}") is None
    assert len(fake_board.setups) == 1


async def test_board_not_ready_repair_clears_on_heartbeat(hass, fake_board, hass_storage, fake_clock):
    fake_board.beating = False
    await _setup(hass, [fake_board], hass_storage)
    fake_clock.now += 40  # no heartbeat
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    issue_id = f"board_not_ready_{fake_board.device_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    fake_board.beat()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
