"""Diagnostics of lamps (signal, last beacon, board) and remotes (last press, misses, lamps)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BleMeshLightsConfigEntry
from .entity import LampEntity, RemoteEntity, setup_remote_entities
from .state import scene_of


async def async_setup_entry(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id in hub.lamps:
        async_add_entities(
            [
                SceneSensor(hub, subentry_id, "scene"),
                LevelSensor(hub, subentry_id, "level"),
                ColorTempSensor(hub, subentry_id, "color_temp"),
                SignalSensor(hub, subentry_id, "rssi"),
                LastBeaconSensor(hub, subentry_id, "last_beacon"),
                BoardSensor(hub, subentry_id, "board"),
            ],
            config_subentry_id=subentry_id,
        )
    setup_remote_entities(
        hub,
        async_add_entities,
        lambda hub, key: [
            LastPressSensor(hub, key, "last_press"),
            MissedSensor(hub, key, "missed"),
            LampsSensor(hub, key, "lamps"),
        ],
    )


class SceneSensor(LampEntity, SensorEntity):
    """The scene the lamp is in, told from its colour temperature and level; "none" otherwise."""

    _attr_translation_key = "scene"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, hub, subentry_id: str, key: str) -> None:
        super().__init__(hub, subentry_id, key)
        self._attr_options = ["none", *self.lamp.profile.scenes]

    @property
    def native_value(self) -> str:
        return scene_of(self.lamp.profile, self.lamp.state) or "none"


class LevelSensor(LampEntity, SensorEntity):
    """The brightness step HA believes the lamp is at (kept while off: the lamp returns to it)."""

    _attr_translation_key = "brightness"
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int | None:
        return self.lamp.state.level


class ColorTempSensor(LampEntity, SensorEntity):
    _attr_translation_key = "color_temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.KELVIN

    @property
    def native_value(self) -> int | None:
        return self.lamp.state.color_temp


class SignalSensor(LampEntity, SensorEntity):
    _attr_translation_key = "signal_strength"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self.subentry_id in self.hub.lamps

    @property
    def native_value(self) -> int | None:
        return self.lamp.rssi


class LastBeaconSensor(LampEntity, SensorEntity):
    _attr_translation_key = "last_beacon"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self.subentry_id in self.hub.lamps

    @property
    def native_value(self) -> datetime | None:
        return self.lamp.last_seen


class BoardSensor(LampEntity, SensorEntity):
    _attr_translation_key = "transmitting_proxy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self.subentry_id in self.hub.lamps

    @property
    def native_value(self) -> str | None:
        for board in self.hub.boards:
            if board.device_id == self.lamp.board:
                return board.name
        return None


class LastPressSensor(RemoteEntity, SensorEntity):
    _attr_translation_key = "last_press"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        return self.remote.last_press if self.remote else None


class MissedSensor(RemoteEntity, SensorEntity):
    _attr_translation_key = "missed_presses"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        return self.remote.missed_total if self.remote else None


class LampsSensor(RemoteEntity, SensorEntity):
    _attr_translation_key = "paired_lamp"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        remote = self.remote
        if remote is None:
            return None
        titles = [self.hub.entry.subentries[i].title for i in remote.lamps if i in self.hub.entry.subentries]
        return ", ".join(titles) or None
