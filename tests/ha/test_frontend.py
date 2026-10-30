"""The integration serves the remote card and loads it in every dashboard."""

from __future__ import annotations

import re

from .conftest import setup_entry


async def test_card_is_served(hass, fake_board, monkeypatch):
    import custom_components.ble_mesh_lights as integration

    urls = []
    monkeypatch.setattr(integration, "add_extra_js_url", lambda hass, url: urls.append(url))
    hass.config.components.add("frontend")
    await setup_entry(hass, [fake_board])
    assert len(urls) == 1
    assert re.fullmatch(r"/ble_mesh_lights/frontend/ble-mesh-remote-card\.js\?v=[0-9a-f]{12}", urls[0])


async def test_setup_without_frontend(hass, fake_board, monkeypatch):
    import custom_components.ble_mesh_lights as integration

    urls = []
    monkeypatch.setattr(integration, "add_extra_js_url", lambda hass, url: urls.append(url))
    entry = await setup_entry(hass, [fake_board])
    assert entry.runtime_data is not None
    assert urls == []
