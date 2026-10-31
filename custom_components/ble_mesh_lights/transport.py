"""ESPHome boards and HA Bluetooth: the only module that talks to either.

A board is an ESPHome device running the ble-adv-tx package. It counts as connected
while its "BLE TX package" sensor is available: ESPHome keeps a board's actions
registered while it is unplugged, so the action alone says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    HEARTBEAT_PACKAGE_VERSION,
    IGNORED_DURATION,
    PACKAGE_SENSOR_NAME,
    PACKAGE_SENSOR_OBJECT_ID,
)

ESPHOME = "esphome"


class BoardUnavailable(HomeAssistantError):
    """No board can send right now."""


@dataclass(frozen=True)
class Board:
    device_id: str
    name: str
    node: str  # ESPHome device_name

    @property
    def prefix(self) -> str:
        return self.node.replace("-", "_")

    @property
    def service(self) -> str:
        return f"{self.prefix}_adv_svc_v1"

    @property
    def setup_service(self) -> str:
        return f"{self.prefix}_setup_svc_v0"


async def async_boards(hass: HomeAssistant, device_ids: list[str]) -> list[Board]:
    """Boards for the given ESPHome device ids; ids that are not ESPHome devices are skipped."""
    devices = dr.async_get(hass)
    boards = []
    for device_id in device_ids:
        device = devices.async_get(device_id)
        if device is None:
            continue
        entry = hass.config_entries.async_get_entry(device.config_entry_id)
        if entry is not None and entry.domain == ESPHOME and entry.data.get("device_name"):
            boards.append(Board(device_id, board_name(device, entry.title), entry.data["device_name"]))
    return boards


def board_name(device: dr.DeviceEntry, fallback: str) -> str:
    """The name HA shows for the board's device."""
    return device.name_by_user or device.name or fallback


def _package_entity(hass: HomeAssistant, board: Board) -> er.RegistryEntry | None:
    for entity in er.async_entries_for_device(
        er.async_get(hass), board.device_id, include_disabled_entities=True
    ):
        if entity.domain == "sensor" and (
            entity.unique_id.endswith(PACKAGE_SENSOR_OBJECT_ID) or entity.original_name == PACKAGE_SENSOR_NAME
        ):
            return entity
    return None


def package_sensor(hass: HomeAssistant, board: Board) -> str | None:
    """Entity id of the board's "BLE TX package" sensor (also when it is disabled)."""
    entity = _package_entity(hass, board)
    return entity.entity_id if entity else None


def package_sensor_disabled(hass: HomeAssistant, board: Board) -> bool:
    """The sensor exists but the user disabled it: then there is no version and no heartbeat."""
    entity = _package_entity(hass, board)
    return entity is not None and entity.disabled


def package_version(hass: HomeAssistant, board: Board) -> str | None:
    """The package version the board reports, or None while it is not connected or has not
    reported yet: before a sensor's first publish ESPHome sends missing_state and HA shows
    `unknown` (components/esphome/sensor.py, native_value)."""
    entity_id = package_sensor(hass, board)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    return state.state


def board_ready(hass: HomeAssistant, board: Board) -> bool:
    """The action exists and the package sensor is available. (The hub adds the heartbeat.)"""
    return hass.services.has_service(ESPHOME, board.service) and package_version(hass, board) is not None


def has_heartbeat(hass: HomeAssistant, board: Board) -> bool:
    """The board's package fires the heartbeat event (version 2 or later)."""
    try:
        return int(package_version(hass, board) or "0") >= HEARTBEAT_PACKAGE_VERSION
    except ValueError:
        return False  # an unknown version: the repair says so, no heartbeat is expected


async def async_setup_receiver(hass: HomeAssistant, board: Board) -> None:
    """Ask the board to forward Mesh adverts as events; it forgets this on every reconnect."""
    if hass.services.has_service(ESPHOME, board.setup_service):
        await hass.services.async_call(
            ESPHOME,
            board.setup_service,
            {"ignored_duration": IGNORED_DURATION, "ignored_cids": [], "ignored_macs": []},
            blocking=True,
        )


async def async_send(hass: HomeAssistant, board: Board, raw: str, repeat: int, duration: int) -> None:
    """Send one advert (hex). The board ignores its own echo for IGNORED_DURATION."""
    if not hass.services.has_service(ESPHOME, board.service):
        raise BoardUnavailable(f"{board.name} has no action {ESPHOME}.{board.service}")
    await hass.services.async_call(
        ESPHOME,
        board.service,
        {
            "raw": raw,
            "duration": float(duration),
            "repeat": float(repeat),
            "ignored_advs": [raw],
            "ignored_duration": IGNORED_DURATION,
        },
        blocking=True,
    )


def last_advert(hass: HomeAssistant, mac: str) -> tuple[bytes, float] | None:
    """The lamp's last advert from any scanner and when it came (monotonic seconds)."""
    info = bluetooth.async_last_service_info(hass, mac, connectable=False)
    if info is None or info.raw is None:
        return None
    return info.raw, info.time


def _board_by_scanner_source(hass: HomeAssistant) -> dict[str, str]:
    """Scanner source (the board's Bluetooth address) → board device id."""
    result = {}
    for entry in hass.config_entries.async_entries(bluetooth.DOMAIN):
        source, device_id = entry.data.get("source"), entry.data.get("source_device_id")
        if source and device_id:
            result[source.upper()] = device_id
    return result


def rssi_by_board(hass: HomeAssistant, mac: str) -> dict[str, int]:
    """Board device id → the RSSI that board last heard the lamp at."""
    boards = _board_by_scanner_source(hass)
    result = {}
    for device in bluetooth.async_scanner_devices_by_address(hass, mac, connectable=False):
        device_id = boards.get(device.scanner.source.upper())
        if device_id is not None:
            result[device_id] = device.advertisement.rssi
    return result


def discovered_beacons(hass: HomeAssistant) -> list[tuple[str, bytes, float]]:
    """(address, raw advert, time) of every device HA Bluetooth currently knows."""
    return [
        (info.address, info.raw, info.time)
        for info in bluetooth.async_discovered_service_info(hass, connectable=False)
        if info.raw is not None
    ]
