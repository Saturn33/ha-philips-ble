"""Websocket command for the remote card: which lamps there are, their profiles and remote layouts."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation

from .const import DOMAIN

LABEL_KEY = f"component.{DOMAIN}.entity.event.button_press.state_attributes.event_type.state.{{}}"


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_lamps)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/lamps",
        vol.Optional("entity_ids"): [str],
        vol.Optional("language"): str,
    }
)
@websocket_api.async_response
async def ws_lamps(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Lamps of the loaded entry (all, or those of `entity_ids`). The client sends its language:
    a websocket handler cannot know the user's (the connection has a user, the user no language;
    HA's own frontend/get_translations takes it from the client the same way)."""
    entry = next(
        (e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED), None
    )
    if entry is None:
        connection.send_result(msg["id"], {"loaded": False, "lamps": {}})
        return
    hub = entry.runtime_data
    names = await translation.async_get_translations(
        hass, msg.get("language") or hass.config.language, "entity", [DOMAIN]
    )
    wanted = msg.get("entity_ids")
    lamps: dict[str, Any] = {}
    for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        lamp = hub.lamps.get(item.config_subentry_id or "")
        if item.domain != "light" or lamp is None or (wanted is not None and item.entity_id not in wanted):
            continue
        profile = lamp.profile
        commands = [name for name in profile.commands if name != profile.pairing.command]
        lamps[item.entity_id] = {
            "profile": profile.id,
            "commands": commands,
            "brightness_levels": list(profile.brightness_levels),
            "remote": profile.remote_layout,
            "labels": {name: names.get(LABEL_KEY.format(name), name) for name in commands},
        }
    connection.send_result(msg["id"], {"loaded": True, "lamps": lamps})
