"""The light entity: stepped brightness, colour temperature and scenes."""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ColorMode,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ble_mesh_lights import hub as hub_module
from custom_components.ble_mesh_lights.const import DOMAIN
from custom_components.ble_mesh_lights.profile import PROFILES_DIR
from custom_components.ble_mesh_lights.state import LampState

from .conftest import L1, ROOT, decode_sent, lamp_subentry, setup_entry

LIGHT = "light.street"


async def _setup(hass, fake_board, hass_storage, **lamp):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry(**lamp)], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    return entry, entry.runtime_data, sub_id


async def _call(hass, service, **data):
    await hass.services.async_call("light", service, {ATTR_ENTITY_ID: LIGHT, **data}, blocking=True)
    await hass.async_block_till_done()


def _names(board):
    return [decode_sent(call)[2] for call in board.sent]


async def test_light_state_from_hub(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    await hass.async_block_till_done()
    state = hass.states.get(LIGHT)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 128
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 2700
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 2200
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 6500
    assert state.attributes[ATTR_EFFECT_LIST] == ["relax", "bright", "concentrate", "energise", "nightlight"]
    assert state.attributes["assumed_state"] is True
    assert state.attributes["state_uncertain"] is False


async def test_unknown_state_shows_unknown(hass, fake_board, hass_storage):
    await _setup(hass, fake_board, hass_storage)
    assert hass.states.get(LIGHT).state == "unknown"


async def test_turn_on_plain_sends_on(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    await _call(hass, "turn_on")
    assert _names(fake_board) == ["on"]


async def test_turn_on_brightness_snaps_to_step(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 150})
    assert _names(fake_board) == ["brightness_down", "brightness_down"]
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 128


@pytest.mark.parametrize(
    ("current", "kelvin", "expected"),
    [(2200, 3500, 4000), (2200, 3300, 3000), (3000, 3100, 4000), (6500, 3300, 3000), (None, 3300, 3000)],
)
async def test_turn_on_kelvin_snaps_in_the_direction_of_the_change(
    hass, fake_board, hass_storage, current, kelvin, expected
):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, current, 100))
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: kelvin})
    assert hub.lamps[l1].state.color_temp == expected


async def test_slider_moved_a_little_up_goes_one_step_up(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 50))
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 153})  # 60 %
    assert _names(fake_board) == ["brightness_up"]
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 204  # 80 %


async def test_slider_on_a_step_keeps_it(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 50))
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 128})  # 50 % through HA's 0-255 scale
    assert fake_board.sent == []


async def test_effect_sends_scene(hass, fake_board, hass_storage):
    await _setup(hass, fake_board, hass_storage)
    await _call(hass, "turn_on", **{ATTR_EFFECT: "relax"})
    assert _names(fake_board) == ["relax"]
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "relax"


async def test_turn_off_sends_off(hass, fake_board, hass_storage):
    await _setup(hass, fake_board, hass_storage)
    await _call(hass, "turn_off")
    assert _names(fake_board) == ["off"]
    assert hass.states.get(LIGHT).state == STATE_OFF


async def test_unavailable_when_unpowered(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.lamps[l1].present = False
    hub.lamp_changed(l1)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE


async def test_device_info(hass, fake_board, hass_storage):
    await _setup(hass, fake_board, hass_storage)
    entity = er.async_get(hass).async_get(LIGHT)
    assert entity.unique_id == L1
    assert entity.config_subentry_id is not None
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.name == "Street"
    assert (device.manufacturer, device.model) == ("Philips", "Valen ceiling light")
    assert (DOMAIN, L1) in device.identifiers


def test_effect_names_translated():
    ru = json.loads((ROOT / "custom_components/ble_mesh_lights/translations/ru.json").read_text())
    effects = ru["entity"]["light"]["lamp"]["state_attributes"]["effect"]["state"]
    assert effects["relax"] == "Отдых"


async def test_wizard_aliases_reach_entity_registry(hass, fake_board, hass_storage):
    entry, _, sub_id = await _setup(hass, fake_board, hass_storage, aliases=["свет на улице"])
    entity = er.async_get(hass).async_get(LIGHT)
    assert entity.aliases == [er.COMPUTED_NAME, "свет на улице"]
    assert entry.subentries[sub_id].data["aliases_applied"] is True
    # Afterwards HA owns them: a change by the user survives a reload.
    er.async_get(hass).async_update_entity(LIGHT, aliases=["street"])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(LIGHT).aliases == ["street"]


async def test_toggle_profile_light(hass, fake_board, hass_storage, tmp_path, monkeypatch):
    shutil.copytree(PROFILES_DIR / "valen", tmp_path / "valen")
    shutil.copytree(ROOT / "tests/fixtures/profiles/toggle", tmp_path / "toggle")
    monkeypatch.setattr(hub_module, "PROFILES_DIR", tmp_path)
    _, hub, l1 = await _setup(hass, fake_board, hass_storage, profile="toggle")
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _call(hass, "turn_off")
    names = [decode_sent(call)[2] for call in fake_board.sent]
    assert names == ["on"]  # the toggle payload is the Valen "on" payload in this test profile
    assert hub.lamps[l1].state.on is False
    await _call(hass, "turn_off")
    assert len(fake_board.sent) == 1  # already off: nothing to toggle


async def test_broken_profile_raises_issue_and_lamp_unavailable(
    hass, fake_board, hass_storage, tmp_path, monkeypatch
):
    shutil.copytree(PROFILES_DIR / "valen", tmp_path / "valen")
    shutil.copytree(ROOT / "tests/fixtures/profiles/broken", tmp_path / "broken")
    monkeypatch.setattr(hub_module, "PROFILES_DIR", tmp_path)
    entry = await setup_entry(
        hass, [fake_board], lamps=[lamp_subentry(profile="gone")], hass_storage=hass_storage
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "profile_errors")
    assert issue is not None
    assert "broken" in issue.translation_placeholders["errors"]
    assert entry.runtime_data.lamps == {}
    assert hass.states.get(LIGHT) is None


async def test_favorite_colors_are_the_lamps_temperatures(hass, fake_board, hass_storage):
    entry, _, sub_id = await _setup(hass, fake_board, hass_storage)
    favorites = er.async_get(hass).async_get(LIGHT).options["light"]["favorite_colors"]
    assert favorites == [{"color_temp_kelvin": k} for k in (2200, 2700, 3000, 4000, 6500)]
    assert entry.subentries[sub_id].data["favorites_applied"] is True
    # Afterwards HA owns them: the user's own choice survives a reload.
    er.async_get(hass).async_update_entity_options(LIGHT, "light", {"favorite_colors": []})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(LIGHT).options["light"]["favorite_colors"] == []


async def test_color_temp_drag_does_not_ping_pong(hass, fake_board, hass_storage, fake_clock):
    """HA's colour temperature slider calls turn_on every 0.5 s while it is dragged: the direction
    counts from where the drag started, not from the step the lamp has just reached."""
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 6500, 100))
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 6300})
    assert hub.lamps[l1].state.color_temp == 4000
    sent = len(fake_board.sent)
    for kelvin in (6000, 5200, 4500):
        fake_clock.now += 0.5
        await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: kelvin})
        assert hub.lamps[l1].state.color_temp == 4000, kelvin
    assert len(fake_board.sent) == sent  # the same step again: nothing sent
    # A new drag later counts from the step the lamp is at now.
    fake_clock.now += 3
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 4200})
    assert hub.lamps[l1].state.color_temp == 6500


async def test_color_temp_drag_up_from_the_warmest(hass, fake_board, hass_storage, fake_clock):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2200, 100))
    for kelvin in (2400, 2500, 2600):
        await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: kelvin})
        assert hub.lamps[l1].state.color_temp == 2700, kelvin
        fake_clock.now += 0.5


async def test_failed_step_is_retried_during_a_drag(hass, fake_board, hass_storage, fake_clock):
    from homeassistant.exceptions import HomeAssistantError

    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 6500, 100))
    fake_board.offline = True
    with pytest.raises(HomeAssistantError):
        await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 6300})
    fake_board.offline = False
    fake_clock.now += 0.5
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 6200})
    assert hub.lamps[l1].state.color_temp == 4000


async def test_brightness_series_does_not_ping_pong(hass, fake_board, hass_storage, fake_clock):
    """A UI or automation that sends brightness in a quick series: the direction counts from the
    level before the series, so values between two steps do not bounce the lamp between them."""
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 50))
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 153})  # 60 %
    assert hub.lamps[l1].state.level == 80
    sent = len(fake_board.sent)
    for brightness in (166, 140, 179):  # 65 %, 55 %, 70 %
        fake_clock.now += 0.5
        await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: brightness})
        assert hub.lamps[l1].state.level == 80, brightness
    assert len(fake_board.sent) == sent
    # A new change later counts from the level the lamp is at now.
    fake_clock.now += 3
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 166})  # 65 %: down from 80
    assert hub.lamps[l1].state.level == 50


async def test_skipped_step_still_publishes_the_state(hass, fake_board, hass_storage, fake_clock):
    """HA's slider ignores state updates while it is dragged and re-reads the state only on the
    next update: the release of a drag must give one, or the slider stays where it was dropped."""
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 3264})
    before = hass.states.get(LIGHT)
    fake_clock.now += 0.5
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 3278})  # same step: nothing sent
    after = hass.states.get(LIGHT)
    assert after.attributes[ATTR_COLOR_TEMP_KELVIN] == 4000
    assert after.last_updated > before.last_updated
    # Not a change of the light: HA's "last changed" (shown on the light's card) stays.
    assert after.last_changed == before.last_changed


async def test_request_for_the_current_step_publishes_the_state(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await hass.async_block_till_done()
    before = hass.states.get(LIGHT)
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 3000})
    assert fake_board.sent == []
    assert hass.states.get(LIGHT).last_updated > before.last_updated


async def test_state_is_published_again_after_a_request(hass, fake_board, hass_storage, fake_clock):
    """The slider may still be held when a request is answered: the state is published again a
    few seconds later, so the slider settles on the step after it is released."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    _, hub, l1 = await _setup(hass, fake_board, hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    await _call(hass, "turn_on", **{ATTR_COLOR_TEMP_KELVIN: 3264})
    start = dt_util.utcnow()
    updates = []
    for seconds in (1.5, 3.5, 6.5, 12):
        before = hass.states.get(LIGHT).last_updated
        async_fire_time_changed(hass, start + timedelta(seconds=seconds))
        await hass.async_block_till_done()
        updates.append(hass.states.get(LIGHT).last_updated > before)
    assert updates == [True, True, True, False]
    assert hass.states.get(LIGHT).last_changed < start


@pytest.mark.parametrize("effect", ["pairing", "nope"])
async def test_unknown_effect_is_rejected(hass, fake_board, hass_storage, effect):
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.ble_mesh_lights.const import STORAGE_SEQUENCE

    await _setup(hass, fake_board, hass_storage)
    counters = dict(hass_storage[STORAGE_SEQUENCE]["data"]["addresses"])
    with pytest.raises(ServiceValidationError):
        await _call(hass, "turn_on", **{ATTR_EFFECT: effect})
    assert fake_board.sent == []
    assert hass_storage[STORAGE_SEQUENCE]["data"]["addresses"] == counters


async def test_same_step_after_a_remote_change_is_sent(hass, fake_board, hass_storage, fake_clock):
    """An automation sets 50 %, the remote then sets 80 %, the automation sets 50 % again within
    2 s: the lamp must go back to 50 %, not be skipped as "the same step again"."""
    from .conftest import fire_raw_adv, remote_press

    _, hub, l1 = await _setup(hass, fake_board, hass_storage, remote="valen:37af")
    hub.set_assumed_state(l1, LampState(True, 3000, 80))
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 128})  # 50 %
    assert hub.lamps[l1].state.level == 50
    fire_raw_adv(hass, remote_press("brightness_up", 0x37AF, 2280), fake_board.device_id)
    await hass.async_block_till_done()
    assert hub.lamps[l1].state.level == 80
    fake_clock.now += 0.5
    await _call(hass, "turn_on", **{ATTR_BRIGHTNESS: 128})
    assert hub.lamps[l1].state.level == 50
    assert _names(fake_board)[-1] == "brightness_down"


async def test_same_step_after_a_remote_cancel_is_sent(hass, fake_board, hass_storage, fake_clock):
    """A chain to 50 % runs, a remote press cancels it while its command is on the air, and the
    same 50 % is asked again within 2 s: it must be sent, not skipped as "the same step"."""
    from .conftest import fire_raw_adv, remote_press, wait_for

    _, hub, l1 = await _setup(hass, fake_board, hass_storage, remote="valen:37af")
    hub.set_assumed_state(l1, LampState(True, 3000, 100))
    fake_board.hold = asyncio.Event()
    first = asyncio.ensure_future(_call(hass, "turn_on", **{ATTR_BRIGHTNESS: 128}))
    await wait_for(lambda: hub.chain_running(l1))
    fire_raw_adv(hass, remote_press("bright", 0x37AF, 2280), fake_board.device_id)  # 3000 K, 100 %
    fake_clock.now += 0.5
    second = asyncio.ensure_future(_call(hass, "turn_on", **{ATTR_BRIGHTNESS: 128}))
    for _ in range(10):
        await asyncio.sleep(0)
    fake_board.hold.set()
    await asyncio.gather(first, second)
    assert hub.lamps[l1].state.level == 50
