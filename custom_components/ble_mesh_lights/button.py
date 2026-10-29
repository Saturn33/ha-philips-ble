"""Remote-style buttons of a lamp: one press, one command."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BleMeshLightsConfigEntry
from .entity import LampEntity

# Command id → translation key
BUTTONS = {
    "brightness_up": "brightness_up",
    "brightness_down": "brightness_down",
    "color_temperature": "next_color_temperature",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id, lamp in hub.lamps.items():
        async_add_entities(
            [
                CommandButton(hub, subentry_id, command)
                for command in BUTTONS
                if command in lamp.profile.commands
            ],
            config_subentry_id=subentry_id,
        )


class CommandButton(LampEntity, ButtonEntity):
    def __init__(self, hub, subentry_id: str, command: str) -> None:
        super().__init__(hub, subentry_id, command)
        self._command = command
        self._attr_translation_key = BUTTONS[command]

    async def async_press(self) -> None:
        await self.hub.async_command(self.subentry_id, self._command)
