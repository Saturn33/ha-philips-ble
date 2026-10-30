"""The websocket command the remote card reads its lamps and layouts from."""

from __future__ import annotations

from .conftest import lamp_subentry, setup_entry


async def _lamps(hass, hass_ws_client, **message):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "ble_mesh_lights/lamps", **message})
    answer = await client.receive_json()
    assert answer["success"], answer
    return answer["result"]


async def test_lamps_command(hass, hass_ws_client, fake_board, hass_storage):
    await setup_entry(
        hass,
        [fake_board],
        lamps=[
            lamp_subentry(),
            lamp_subentry(mac="E8:26:CF:00:00:02", address=0x7E02, index=2, title="Patio"),
        ],
        hass_storage=hass_storage,
    )
    hass.states.async_set("light.kitchen_strip", "on")
    for message in ({"entity_ids": ["light.street", "light.patio", "light.kitchen_strip"]}, {}):
        result = await _lamps(hass, hass_ws_client, **message)
        assert result["loaded"] is True
        assert set(result["lamps"]) == {"light.street", "light.patio"}
        street = result["lamps"]["light.street"]
        assert street["profile"] == "valen"
        assert "pairing" not in street["commands"] and "relax" in street["commands"]
        assert street["brightness_levels"] == [100, 80, 50, 30, 10, 5]
        assert set(street["remote"]["layouts"]) == {"vertical", "horizontal", "mini"}
        assert street["labels"]["relax"] == "Relax"
    result = await _lamps(hass, hass_ws_client, entity_ids=["light.street"], language="ru")
    assert result["lamps"]["light.street"]["labels"]["relax"] == "Отдых"


async def test_lamps_command_while_reloading(hass, hass_ws_client, fake_board, hass_storage):
    entry = await setup_entry(hass, [fake_board], lamps=[lamp_subentry()], hass_storage=hass_storage)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    result = await _lamps(hass, hass_ws_client, entity_ids=["light.street"])
    assert result == {"loaded": False, "lamps": {}}
