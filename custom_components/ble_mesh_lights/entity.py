"""Base class for the entities of one lamp."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_LAMP, SIGNAL_REMOTE, SIGNAL_REMOTE_ADDED
from .hub import Hub, LampRuntime, RemoteInfo
from .remotes import RemotePress

LAMP_AREA = "area"


class LampEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Hub, subentry_id: str, key: str | None = None) -> None:
        self.hub = hub
        self.subentry_id = subentry_id
        lamp = hub.lamps[subentry_id]
        subentry = hub.entry.subentries[subentry_id]
        area = None
        if area_id := subentry.data.get(LAMP_AREA):
            area_entry = ar.async_get(hub.hass).async_get_area(area_id)
            area = area_entry.name if area_entry else None
        self._attr_unique_id = lamp.mac if key is None else f"{lamp.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, lamp.mac)},
            connections={(CONNECTION_BLUETOOTH, lamp.mac)},
            name=subentry.title,
            manufacturer=lamp.profile.manufacturer,
            model=lamp.profile.model,
            # The Mesh address HA sends to this lamp as (base address + the lamp's index).
            serial_number=f"0x{lamp.address:04X}",
            suggested_area=area,
        )

    @property
    def lamp(self) -> LampRuntime:
        return self.hub.lamps[self.subentry_id]

    @property
    def available(self) -> bool:
        return self.subentry_id in self.hub.lamps and self.lamp.present is not False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_LAMP.format(self.subentry_id), self.async_write_ha_state
            )
        )


class RemoteEntity(Entity):
    """An entity of a remote's device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Hub, key: str, suffix: str) -> None:
        self.hub = hub
        self.key = key
        remote = hub.remotes[key]
        profile = hub.profiles.get(remote.profile)
        self._attr_unique_id = f"remote_{key}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"remote_{key}")},
            translation_key="remote",
            translation_placeholders=hub.remote_device_name(key),
            manufacturer=profile.manufacturer if profile else None,
            # The Mesh address the remote sends as: the same one the "New remote" repair shows.
            serial_number=f"0x{remote.source:04X}",
        )

    @property
    def remote(self) -> RemoteInfo | None:
        return self.hub.remotes.get(self.key)

    @property
    def available(self) -> bool:
        return self.key in self.hub.remotes

    @callback
    def handle_press(self, press: RemotePress | None) -> None:
        """A press (None: the remote's binding changed)."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_REMOTE.format(self.key), self.handle_press)
        )


def setup_remote_entities(
    hub: Hub, add: Callable[[Iterable[Entity]], None], factory: Callable[[Hub, str], Iterable[Entity]]
) -> None:
    """Entities for every known remote now, and for each remote added later."""
    for key in list(hub.remotes):
        add(factory(hub, key))

    @callback
    def _added(key: str) -> None:
        add(factory(hub, key))

    hub.entry.async_on_unload(async_dispatcher_connect(hub.hass, SIGNAL_REMOTE_ADDED, _added))
