"""Lamp power (from its beacon) and whether HA's idea of its state is uncertain."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BleMeshLightsConfigEntry
from .entity import LampEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id in hub.lamps:
        async_add_entities(
            [PowerSensor(hub, subentry_id, "power"), UncertainSensor(hub, subentry_id, "uncertain")],
            config_subentry_id=subentry_id,
        )


class PowerSensor(LampEntity, BinarySensorEntity):
    _attr_translation_key = "power"
    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def available(self) -> bool:
        return self.subentry_id in self.hub.lamps  # stays available when the lamp has no power

    @property
    def is_on(self) -> bool | None:
        return self.lamp.present


class UncertainSensor(LampEntity, BinarySensorEntity):
    _attr_translation_key = "state_uncertain"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.lamp.uncertain
