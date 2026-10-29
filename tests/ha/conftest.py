"""Fixtures for the Home Assistant suite: fake ESPHome boards, fake air, a settable clock."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ble_mesh_lights as integration  # noqa: F401  (import the package first)
from custom_components.ble_mesh_lights import hub as hub_module
from custom_components.ble_mesh_lights import transport
from custom_components.ble_mesh_lights.const import (
    CONF_BASE_ADDRESS,
    CONF_BOARDS,
    CONF_COMMAND_INTERVAL,
    DATA_USED_INDICES,
    DOMAIN,
    HEARTBEAT_EVENT,
    RAW_ADV_EVENT,
    STORAGE_SEQUENCE,
    STORAGE_VERSION,
    SUBENTRY_LAMP,
)
from custom_components.ble_mesh_lights.mesh import MeshCodec
from custom_components.ble_mesh_lights.profile import PROFILES_DIR, load_profile

pytest_plugins = ["pytest_homeassistant_custom_component"]

ROOT = Path(__file__).resolve().parents[2]
L1 = "E8:26:CF:54:65:AD"


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, mock_bluetooth):
    """Every HA test loads custom integrations and runs with mocked Bluetooth."""


@pytest.fixture(autouse=True)
def no_deprecated_use(caplog):
    """HA reports deprecated API use by a custom integration only as a log line: fail on it."""
    yield
    records = caplog.get_records("setup") + caplog.get_records("call")
    reports = [r.getMessage() for r in records if "custom integration 'ble_mesh_lights'" in r.getMessage()]
    assert reports == []


@dataclass
class FakeBoard:
    hass: HomeAssistant
    node: str
    name: str
    device_id: str
    version_entity_id: str
    sent: list[dict[str, Any]] = field(default_factory=list)
    setups: list[dict[str, Any]] = field(default_factory=list)
    offline: bool = False
    beating: bool = True  # sends its heartbeat whenever the fake clock moves
    hold: asyncio.Event | None = None  # while set and not released, a send stays on the air
    version: str = "2"

    @property
    def prefix(self) -> str:
        return self.node.replace("-", "_")

    def beat(self) -> None:
        """The board's heartbeat event (every 10 s on package 2), as HA's ESPHome integration fires it."""
        self.hass.bus.async_fire(HEARTBEAT_EVENT, {"device_id": self.device_id, "version": self.version})

    def set_connected(self, connected: bool, version: str | None = None) -> None:
        """Drive the "BLE TX package" sensor, which is how the hub sees (re)connects."""
        self.offline = not connected
        if version is not None:
            self.version = version
        self.hass.states.async_set(self.version_entity_id, self.version if connected else "unavailable")


@pytest.fixture
def make_board(hass: HomeAssistant):
    """Factory for fake ESPHome boards with the ble-adv-tx actions and version sensor."""

    def _make(node: str = "ble-proxy-tx-kitchen", name: str = "BLE Proxy TX (Kitchen)", version: str = "2"):
        esphome_entry = MockConfigEntry(domain="esphome", data={"device_name": node}, title=name)
        esphome_entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=esphome_entry.entry_id, identifiers={("esphome", node)}, name=name
        )
        entity = er.async_get(hass).async_get_or_create(
            "sensor",
            "esphome",
            f"{node}-text_sensor-ble_tx_package",
            device_id=device.id,
            config_entry=esphome_entry,
            original_name="BLE TX package",
        )
        board = FakeBoard(hass, node, name, device.id, entity.entity_id, version=version)
        _BOARDS.append(board)

        async def adv(call: ServiceCall) -> None:
            await asyncio.sleep(0)  # a real action goes over the network: other tasks run meanwhile
            if board.hold is not None:
                await board.hold.wait()
            if board.offline:
                raise HomeAssistantError("action_call_failed")
            board.sent.append(dict(call.data))

        async def setup(call: ServiceCall) -> None:
            if board.offline:
                raise HomeAssistantError("action_call_failed")
            board.setups.append(dict(call.data))

        hass.services.async_register("esphome", f"{board.prefix}_adv_svc_v1", adv)
        hass.services.async_register("esphome", f"{board.prefix}_setup_svc_v0", setup)
        hass.states.async_set(board.version_entity_id, version)
        return board

    return _make


@pytest.fixture
def fake_board(make_board) -> FakeBoard:
    return make_board()


class FakeAir:
    """Stands in for HA Bluetooth: last adverts, RSSI per board, discovered beacons."""

    def __init__(self) -> None:
        self.adverts: dict[str, tuple[bytes, float]] = {}
        self.rssi: dict[str, dict[str, int]] = {}

    def beacon(self, mac: str, raw: bytes, time: float, rssi: int = -50, board: str | None = None) -> None:
        self.adverts[mac] = (raw, time)
        if board is not None:
            self.rssi.setdefault(mac, {})[board] = rssi

    def forget(self, mac: str) -> None:
        self.adverts.pop(mac, None)
        self.rssi.pop(mac, None)


@pytest.fixture
def fake_air(monkeypatch) -> FakeAir:
    air = FakeAir()
    monkeypatch.setattr(transport, "last_advert", lambda hass, mac: air.adverts.get(mac))
    monkeypatch.setattr(transport, "rssi_by_board", lambda hass, mac: dict(air.rssi.get(mac, {})))
    monkeypatch.setattr(
        transport,
        "discovered_beacons",
        lambda hass: [(mac, raw, time) for mac, (raw, time) in air.adverts.items()],
    )
    return air


# Boards made in the current test. A beating board (the default) sends its heartbeat whenever the
# fake clock moves, as a real one does every 10 s; tests about a missing heartbeat set beating False.
_BOARDS: list[FakeBoard] = []


@pytest.fixture(autouse=True)
def _forget_boards():
    yield
    _BOARDS.clear()


class FakeClock:
    def __init__(self) -> None:
        self._now = 1000.0

    @property
    def now(self) -> float:
        return self._now

    @now.setter
    def now(self, value: float) -> None:
        self._now = value
        for board in _BOARDS:
            if board.beating and not board.offline:
                board.beat()

    def __call__(self) -> float:
        return self._now


@pytest.fixture
def fake_clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(hub_module, "monotonic", clock)
    return clock


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Rate limits and pauses do not slow the tests down; awaited durations are recorded, other
    tasks get a turn, and a fake clock (if used) moves on by the duration."""
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)
        if isinstance(hub_module.monotonic, FakeClock):
            hub_module.monotonic.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(hub_module, "sleep", _sleep)
    return waits


def lamp_subentry(title: str = "Street", mac: str = L1, address: int = 0x7E01, index: int = 1, **extra):
    data = {
        "profile": "valen",
        "mac": mac,
        "address": address,
        "index": index,
        "after_outage": "nothing",
        "aliases": [],
        "remote": None,
        **extra,
    }
    return ConfigSubentryData(data=data, subentry_type=SUBENTRY_LAMP, title=title, unique_id=mac)


def seed_counters(hass_storage, values: dict[int, int]) -> None:
    """A sequence store on disk with these counters."""
    hass_storage[STORAGE_SEQUENCE] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_SEQUENCE,
        "data": {"version": 1, "addresses": {f"0x{a:04x}": v for a, v in values.items()}},
    }


async def setup_entry(
    hass: HomeAssistant,
    boards: list[FakeBoard],
    lamps=(),
    options=None,
    hass_storage=None,
    counters: dict[int, int] | None = None,
) -> MockConfigEntry:
    """Create and load the integration's entry. Lamps get a sequence store unless counters is {}."""
    lamps = list(lamps)
    if hass_storage is not None and lamps and counters != {}:
        seed_counters(hass_storage, counters or {sub["data"]["address"]: 0 for sub in lamps})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="BLE Mesh Lights",
        data={DATA_USED_INDICES: sorted(sub["data"]["index"] for sub in lamps)},
        options={
            CONF_BOARDS: [b.device_id for b in boards],
            CONF_BASE_ADDRESS: 0x7E00,
            CONF_COMMAND_INTERVAL: 0.5,
            **(options or {}),
        },
        subentries_data=lamps,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def load_fixture_raw(prefix: str, mac: str = L1) -> bytes:
    """First advert of the given kind from the anonymised capture fixture."""
    for line in (ROOT / "tests" / "fixtures" / "valen_air.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["mac"] == mac and record["raw"].startswith(prefix):
            return bytes.fromhex(record["raw"])
    raise LookupError(prefix)


VALEN = load_profile(PROFILES_DIR / "valen" / "profile.yaml")
CODEC = MeshCodec(VALEN.net_key, VALEN.app_key, VALEN.iv_index)


def fire_raw_adv(hass: HomeAssistant, raw: bytes, device_id: str, orig: str = "E8:26:CF:6B:37:AF") -> None:
    """A Mesh advert heard by a board, as the ble_adv_proxy component reports it."""
    hass.bus.async_fire(RAW_ADV_EVENT, {"device_id": device_id, "orig": orig, "raw": raw.hex()})


def remote_press(command: str, source: int, sequence: int, transaction: int = 0x80) -> bytes:
    """The advert a remote sends for one press."""
    payload = VALEN.commands[command].payload(transaction)
    return CODEC.encode(payload, sequence, source, VALEN.destination, VALEN.ttl)


def decode_sent(call: dict[str, Any]) -> tuple[int, int, str]:
    """(source, sequence, command name) of one recorded adv_svc_v1 call."""
    (message,) = CODEC.decode(bytes.fromhex(call["raw"]))
    return message.source, message.sequence, VALEN.identify(message.payload)


async def wait_for(predicate, rounds: int = 100) -> None:
    """Let other tasks run until predicate() holds."""
    for _ in range(rounds):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")
