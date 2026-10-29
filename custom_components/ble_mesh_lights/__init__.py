"""BLE Mesh Lights: remote-only Bluetooth Mesh lights through ESPHome boards."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .hub import Hub
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Subentry data the integration writes itself and that changes nothing at runtime.
NO_RELOAD_KEYS = frozenset({"aliases_applied", "favorites_applied"})

type BleMeshLightsConfigEntry = ConfigEntry[Hub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


def _reload_key(entry: ConfigEntry) -> tuple[Any, ...]:
    """What a reload depends on: options and lamp subentries, never entry.data alone."""
    subentries = {
        sub_id: (sub.title, sub.unique_id, {k: v for k, v in sub.data.items() if k not in NO_RELOAD_KEYS})
        for sub_id, sub in entry.subentries.items()
    }
    return dict(entry.options), subentries


async def async_setup_entry(hass: HomeAssistant, entry: BleMeshLightsConfigEntry) -> bool:
    hub = Hub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    loaded_key = _reload_key(entry)

    async def _updated(hass: HomeAssistant, entry: BleMeshLightsConfigEntry) -> None:
        if _reload_key(entry) != loaded_key:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BleMeshLightsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, device: DeviceEntry
) -> bool:
    """A remote's device can be deleted (the remote is forgotten); a lamp is deleted as a subentry."""
    if entry.state is not ConfigEntryState.LOADED:
        return False
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.startswith("remote_"):
            await entry.runtime_data.async_remove_remote(
                identifier.removeprefix("remote_"), remove_device=False
            )
            return True
    return False
