"""A remote's button presses as events."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BleMeshLightsConfigEntry
from .entity import RemoteEntity, setup_remote_entities
from .remotes import RemotePress


async def async_setup_entry(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_remote_entities(
        entry.runtime_data, async_add_entities, lambda hub, key: [ButtonPressEvent(hub, key, "press")]
    )


class ButtonPressEvent(RemoteEntity, EventEntity):
    _attr_translation_key = "button_press"

    def __init__(self, hub, key: str, suffix: str) -> None:
        super().__init__(hub, key, suffix)
        profile = hub.profiles.get(hub.remotes[key].profile)
        self._attr_event_types = list(profile.events) if profile else []

    @callback
    def handle_press(self, press: RemotePress | None) -> None:
        if press is not None and press.command in self._attr_event_types:
            self._trigger_event(
                press.command, {"missed": press.missed, "repeat": press.repeat, "late": press.late}
            )
        self.async_write_ha_state()
