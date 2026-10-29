"""ESPHome boards and HA Bluetooth: the only module that talks to either.

A board is an ESPHome device running the ble-adv-tx package. It counts as connected
while its "BLE TX package" sensor is available: ESPHome keeps a board's actions
registered while it is unplugged, so the action alone says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import IGNORED_DURATION, PACKAGE_SENSOR_NAME, PACKAGE_SENSOR_OBJECT_ID

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
            name = device.name_by_user or device.name or entry.title
            boards.append(Board(device_id, name, entry.data["device_name"]))
    return boards


def package_sensor(hass: HomeAssistant, board: Board) -> str | None:
    """Entity id of the board's "BLE TX package" sensor."""
    for entity in er.async_entries_for_device(er.async_get(hass), board.device_id):
        if entity.domain == "sensor" and (
            entity.unique_id.endswith(PACKAGE_SENSOR_OBJECT_ID) or entity.original_name == PACKAGE_SENSOR_NAME
        ):
            return entity.entity_id
    return None


def package_version(hass: HomeAssistant, board: Board) -> str | None:
    """The package version the board reports, or None while it is not connected."""
    entity_id = package_sensor(hass, board)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.state == STATE_UNAVAILABLE:
        return None
    return state.state


# A board's Bluetooth proxy forwards adverts many times a second; this long without one, the board
# is taken as gone even though HA has not noticed the lost connection yet (that can take a minute,
# and a command sent meanwhile is lost without an error).
SCANNER_SILENT_LIMIT = 15.0


def board_ready(hass: HomeAssistant, board: Board) -> bool:
    if not hass.services.has_service(ESPHOME, board.service) or package_version(hass, board) is None:
        return False
    silent = scanner_silent_for(hass, board)
    return silent is None or silent < SCANNER_SILENT_LIMIT


def _scanner_silent_for(hass: HomeAssistant, board: Board) -> float | None:
    """Seconds since the board's Bluetooth scanner last reported an advert; None without a scanner."""
    for source, device_id in _board_by_scanner_source(hass).items():
        if device_id == board.device_id:
            scanner = bluetooth.async_scanner_by_source(hass, source)
            return None if scanner is None else scanner.time_since_last_detection()
    return None


scanner_silent_for = _scanner_silent_for


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
