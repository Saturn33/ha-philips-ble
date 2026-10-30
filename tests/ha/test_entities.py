"""Power, diagnostics, buttons and the remote devices with their events."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.ble_mesh_lights.const import DOMAIN
from custom_components.ble_mesh_lights.state import LampState

from .conftest import L1, decode_sent, fire_raw_adv, lamp_subentry, remote_press, setup_entry

R1 = "valen:37af"
ACTIVE = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8000001000000000000")


def _device(hass, identifier):
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)


def _entity(hass, platform, unique_id):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def _setup(hass, boards, hass_storage, **lamp):
    entry = await setup_entry(hass, boards, lamps=[lamp_subentry(**lamp)], hass_storage=hass_storage)
    (sub_id,) = entry.subentries
    return entry, entry.runtime_data, sub_id


async def test_lamp_entity_names(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage)
    assert _entity(hass, "binary_sensor", f"{L1}_power") == "binary_sensor.street_power"
    assert _entity(hass, "binary_sensor", f"{L1}_uncertain") == "binary_sensor.street_state_uncertain"
    assert _entity(hass, "button", f"{L1}_brightness_up") == "button.street_brightness_up"
    assert _entity(hass, "button", f"{L1}_brightness_down") == "button.street_brightness_down"
    assert _entity(hass, "button", f"{L1}_color_temperature") == "button.street_next_color_temperature"
    assert _entity(hass, "sensor", f"{L1}_rssi") == "sensor.street_signal_strength"
    assert _entity(hass, "sensor", f"{L1}_last_beacon") == "sensor.street_last_beacon"
    assert _entity(hass, "sensor", f"{L1}_board") == "sensor.street_transmitting_proxy"


async def test_power_sensor_follows_presence(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    power = "binary_sensor.street_power"
    assert hass.states.get(power).state == STATE_UNKNOWN
    hub.lamps[l1].present = True
    hub.lamp_changed(l1)
    await hass.async_block_till_done()
    assert hass.states.get(power).state == STATE_ON
    hub.lamps[l1].present = False
    hub.lamp_changed(l1)
    await hass.async_block_till_done()
    assert hass.states.get(power).state == STATE_OFF  # available while the light is not


async def test_uncertain_sensor(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.lamps[l1].uncertain = True
    hub.lamp_changed(l1)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.street_state_uncertain").state == STATE_ON


async def test_buttons_send_commands(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 3000, 80))
    for button in ("brightness_up", "brightness_down", "next_color_temperature"):
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: f"button.street_{button}"}, blocking=True
        )
    assert [decode_sent(call)[2] for call in fake_board.sent] == [
        "brightness_up",
        "brightness_down",
        "color_temperature",
    ]


async def test_signal_and_board_sensors(hass, fake_board, hass_storage, fake_air, fake_clock):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    fake_air.beacon(L1, ACTIVE, fake_clock.now, rssi=-61, board=fake_board.device_id)
    fake_clock.now += 2
    hub.presence_tick()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.street_signal_strength").state == "-61"
    assert hass.states.get("sensor.street_transmitting_proxy").state == "BLE Proxy TX (Kitchen)"
    assert hass.states.get("sensor.street_last_beacon").state not in (STATE_UNKNOWN, None)


async def _press(hass, board, command, sequence, source=0x37AF):
    fire_raw_adv(hass, remote_press(command, source, sequence), board.device_id)
    await hass.async_block_till_done()


async def test_remote_device_and_entities(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, remote=R1)
    device = _device(hass, f"remote_{R1}")
    assert device is not None
    assert device.name == "Valen remote (Street)"
    lamps = _entity(hass, "sensor", f"remote_{R1}_lamps")
    assert hass.states.get(lamps).state == "Street"


async def test_remote_press_fires_event(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage, remote=R1)
    event = _entity(hass, "event", f"remote_{R1}_press")
    await _press(hass, fake_board, "off", 2280)
    state = hass.states.get(event)
    assert state.attributes["event_type"] == "off"
    assert state.attributes["missed"] == 0
    assert state.attributes["repeat"] is False
    assert state.attributes["late"] is False
    assert "relax" in state.attributes["event_types"]
    assert hass.states.get(_entity(hass, "sensor", f"remote_{R1}_last_press")).state != STATE_UNKNOWN


async def test_remote_missed_counter(hass, fake_board, hass_storage):
    entry, _, _ = await _setup(hass, [fake_board], hass_storage, remote=R1)
    missed = _entity(hass, "sensor", f"remote_{R1}_missed")
    await _press(hass, fake_board, "on", 2280)
    await _press(hass, fake_board, "off", 2310)
    assert hass.states.get(missed).state == "2"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(missed).state == "2"


async def test_remote_added_later_gets_entities(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    await hub.async_bind_remote(R1, [l1])
    await hass.async_block_till_done()
    assert _entity(hass, "event", f"remote_{R1}_press")


async def test_rebinding_updates_remote_name(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, remote=R1)
    await hub.async_bind_remote(R1, [])
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, "sensor", f"remote_{R1}_lamps")).state == STATE_UNKNOWN


async def test_remote_device_removed(hass, fake_board, hass_storage, hass_ws_client):
    entry, hub, _ = await _setup(hass, [fake_board], hass_storage, remote=R1)
    assert await async_setup_component(hass, "config", {})
    device = _device(hass, f"remote_{R1}")
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert R1 not in hub.remotes
    assert _device(hass, f"remote_{R1}") is None


async def test_lamp_device_cannot_be_removed_directly(hass, fake_board, hass_storage, hass_ws_client):
    entry, _, _ = await _setup(hass, [fake_board], hass_storage)
    assert await async_setup_component(hass, "config", {})
    device = _device(hass, L1)
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    assert not response["success"]


async def test_removing_remote_in_options_removes_its_device(hass, fake_board, hass_storage):
    _, hub, _ = await _setup(hass, [fake_board], hass_storage, remote=R1)
    await hub.async_remove_remote(R1)
    await hass.async_block_till_done()
    assert _device(hass, f"remote_{R1}") is None


async def test_remote_device_shows_its_address(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage, remote=R1)
    assert _device(hass, f"remote_{R1}").serial_number == "0x37AF"


async def test_brightness_and_color_temperature_sensors(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    brightness = _entity(hass, "sensor", f"{L1}_level")
    temperature = _entity(hass, "sensor", f"{L1}_color_temp")
    assert (brightness, temperature) == ("sensor.street_brightness", "sensor.street_color_temperature")
    assert hass.states.get(brightness).state == STATE_UNKNOWN
    hub.set_assumed_state(l1, LampState(True, 2700, 30))
    await hass.async_block_till_done()
    assert hass.states.get(brightness).state == "30"
    assert hass.states.get(brightness).attributes["unit_of_measurement"] == "%"
    assert hass.states.get(temperature).state == "2700"
    assert hass.states.get(temperature).attributes["unit_of_measurement"] == "K"
    # Off: the light remembers its brightness and temperature and returns to them when switched on.
    hub.set_assumed_state(l1, LampState(False, 2700, 30))
    await hass.async_block_till_done()
    assert hass.states.get(brightness).state == "30"


async def test_lamp_device_shows_mac_and_sender_address(hass, fake_board, hass_storage):
    await _setup(hass, [fake_board], hass_storage)
    device = _device(hass, L1)
    assert (dr.CONNECTION_BLUETOOTH, L1) in device.connections
    assert device.serial_number == "0x7E01"  # the address HA sends to this lamp as


async def test_scene_sensor(hass, fake_board, hass_storage):
    _, hub, l1 = await _setup(hass, [fake_board], hass_storage, remote=R1)
    scene = _entity(hass, "sensor", f"{L1}_scene")
    assert scene == "sensor.street_scene"
    state = hass.states.get(scene)
    assert state.state == "none"
    assert state.attributes["options"] == ["none", "relax", "bright", "concentrate", "energise", "nightlight"]
    await _press(hass, fake_board, "relax", 2280)  # from the remote
    assert hass.states.get(scene).state == "relax"
    assert hass.states.get("light.street").attributes["effect"] == "relax"
    await _press(hass, fake_board, "brightness_down", 2290)
    assert hass.states.get(scene).state == "none"  # no scene any more


async def test_scene_entities_follow_parameters(hass, fake_board, hass_storage):
    from custom_components.ble_mesh_lights.state import LampState

    _, hub, l1 = await _setup(hass, [fake_board], hass_storage)
    hub.set_assumed_state(l1, LampState(True, 2700, 50))
    await hass.async_block_till_done()
    assert hass.states.get("light.street").attributes["effect"] == "relax"
    assert hass.states.get("sensor.street_scene").state == "relax"
    hub.set_assumed_state(l1, LampState(True, 3000, 100))  # what the lamp does at power-on
    await hass.async_block_till_done()
    assert hass.states.get("sensor.street_scene").state == "bright"
