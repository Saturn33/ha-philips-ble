"""Actions on BLE Mesh lights: correct HA's idea of the state, send a command, skip counters."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN
from .sequence import SKIP


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for name, func, schema in (
        (
            "set_assumed_state",
            "async_set_assumed_state",
            {
                vol.Optional("on"): cv.boolean,
                vol.Optional("color_temp_kelvin"): vol.All(vol.Coerce(int), vol.Range(min=1000, max=10000)),
                vol.Optional("brightness"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            },
        ),
        (
            "send_command",
            "async_send_command",
            {vol.Required("command"): cv.string, vol.Optional("queue", default=False): cv.boolean},
        ),
        (
            "skip_sequence",
            "async_skip_sequence",
            {vol.Optional("amount", default=SKIP): vol.All(vol.Coerce(int), vol.Range(min=1, max=0x800000))},
        ),
    ):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain="light",
            func=func,
            schema=schema,
            # The card asks send_command (with queue) which lamps the command reached.
            supports_response=SupportsResponse.OPTIONAL if name == "send_command" else SupportsResponse.NONE,
        )
