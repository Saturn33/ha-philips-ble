"""ESPHome boards and HA Bluetooth through transport.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components import bluetooth
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ble_mesh_lights import transport
from custom_components.ble_mesh_lights.transport import Board, BoardUnavailable

from .conftest import L1


async def test_boards_resolve_node_name(hass, fake_board):
    (board,) = await transport.async_boards(hass, [fake_board.device_id, "no-such-device"])
    assert board == Board(fake_board.device_id, "BLE Proxy TX (Kitchen)", "ble-proxy-tx-kitchen")
    assert board.service == "ble_proxy_tx_kitchen_adv_svc_v1"


async def test_board_ready_needs_connected_sensor(hass, fake_board):
    (board,) = await transport.async_boards(hass, [fake_board.device_id])
    assert transport.board_ready(hass, board)
    fake_board.set_connected(False)
    assert not transport.board_ready(hass, board)


async def test_send_calls_action(hass, fake_board):
    (board,) = await transport.async_boards(hass, [fake_board.device_id])
    await transport.async_send(hass, board, "0201", repeat=3, duration=100)
    assert fake_board.sent == [
        {
            "raw": "0201",
            "duration": 100.0,
            "repeat": 3.0,
            "ignored_advs": ["0201"],
            "ignored_duration": 20000.0,
        }
    ]


async def test_send_without_action_raises(hass, fake_board):
    (board,) = await transport.async_boards(hass, [fake_board.device_id])
    hass.services.async_remove("esphome", board.service)
    with pytest.raises(BoardUnavailable):
        await transport.async_send(hass, board, "0201", repeat=3, duration=100)


async def test_setup_receiver(hass, fake_board):
    (board,) = await transport.async_boards(hass, [fake_board.device_id])
    await transport.async_setup_receiver(hass, board)
    assert fake_board.setups == [{"ignored_duration": 20000.0, "ignored_cids": [], "ignored_macs": []}]


async def test_rssi_map_by_scanner_source(hass, monkeypatch):
    for source, device in (("B0:A6:04:4D:82:ED", "device_a"), ("B0:A6:04:00:00:01", "device_b")):
        MockConfigEntry(
            domain="bluetooth",
            unique_id=source,
            data={"source": source, "source_domain": "esphome", "source_device_id": device},
        ).add_to_hass(hass)

    def scanner_device(source, rssi):
        advertisement = SimpleNamespace(rssi=rssi)
        return SimpleNamespace(scanner=SimpleNamespace(source=source), advertisement=advertisement)

    devices = [
        scanner_device("B0:A6:04:4D:82:ED", -48),
        scanner_device("B0:A6:04:00:00:01", -71),
        scanner_device("00:00:00:00:00:00", -30),
    ]  # a scanner of no board (local adapter)
    monkeypatch.setattr(bluetooth, "async_scanner_devices_by_address", lambda hass, mac, connectable: devices)
    assert transport.rssi_by_board(hass, L1) == {"device_a": -48, "device_b": -71}


async def test_last_advert_returns_raw_and_time(hass, monkeypatch):
    info = SimpleNamespace(raw=b"\x02\x01\x06", time=1234.5)
    monkeypatch.setattr(bluetooth, "async_last_service_info", lambda hass, mac, connectable: info)
    assert transport.last_advert(hass, L1) == (b"\x02\x01\x06", 1234.5)
    monkeypatch.setattr(bluetooth, "async_last_service_info", lambda hass, mac, connectable: None)
    assert transport.last_advert(hass, L1) is None


async def test_discovered_beacons(hass, monkeypatch):
    infos = [
        SimpleNamespace(address=L1, raw=b"\x02\x01\x06", time=10.0),
        SimpleNamespace(address="X", raw=None, time=1.0),
    ]
    monkeypatch.setattr(bluetooth, "async_discovered_service_info", lambda hass, connectable: infos)
    assert transport.discovered_beacons(hass) == [(L1, b"\x02\x01\x06", 10.0)]
