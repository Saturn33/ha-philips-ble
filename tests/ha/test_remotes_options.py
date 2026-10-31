"""Remotes in the integration options and the "New remote" repair."""

from __future__ import annotations

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.ble_mesh_lights.const import DOMAIN

from .conftest import fire_raw_adv, lamp_subentry, remote_press, setup_entry

R1 = "valen:37af"
R2 = "valen:13b3"
L2 = "E8:26:CF:00:00:02"


async def _setup(hass, fake_board, hass_storage):
    entry = await setup_entry(
        hass,
        [fake_board],
        lamps=[lamp_subentry(), lamp_subentry("Hall", mac=L2, address=0x7E02, index=2)],
        hass_storage=hass_storage,
    )
    ids = {sub.title: sub_id for sub_id, sub in entry.subentries.items()}
    return entry, entry.runtime_data, ids["Street"], ids["Hall"]


async def _hear(hass, board, source, sequence=100):
    fire_raw_adv(hass, remote_press("on", source, sequence), board.device_id)
    await hass.async_block_till_done()


async def _open_remote(hass, entry, key):
    options = hass.config_entries.options
    result = await options.async_init(entry.entry_id)
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remotes"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "remotes"
    result = await options.async_configure(result["flow_id"], {"remote": key})
    assert result["type"] is FlowResultType.MENU
    return result


async def test_bind_unknown_remote(hass, fake_board, hass_storage):
    entry, hub, street, _ = await _setup(hass, fake_board, hass_storage)
    await _hear(hass, fake_board, 0x13B3)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is not None
    result = await _open_remote(hass, entry, R2)
    assert set(result["menu_options"]) == {"remote_lamps", "remote_ignore"}
    options = hass.config_entries.options
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remote_lamps"})
    result = await options.async_configure(result["flow_id"], {"lamps": [street]})
    assert result["type"] is FlowResultType.ABORT
    assert hub.remotes[R2].lamps == [street]
    assert R2 not in hub.unknown_remotes
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None


async def test_rebind_and_remove_remote(hass, fake_board, hass_storage):
    entry, hub, street, hall = await _setup(hass, fake_board, hass_storage)
    await hub.async_bind_remote(R1, [street])
    options = hass.config_entries.options
    result = await _open_remote(hass, entry, R1)
    assert set(result["menu_options"]) == {"remote_lamps", "remote_remove"}
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remote_lamps"})
    result = await options.async_configure(result["flow_id"], {"lamps": [street, hall]})
    assert hub.remotes[R1].lamps == [street, hall]
    result = await _open_remote(hass, entry, R1)
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remote_remove"})
    assert result["type"] is FlowResultType.ABORT
    assert R1 not in hub.remotes


async def test_options_remotes_does_not_reload_entry(hass, fake_board, hass_storage):
    entry, hub, street, _ = await _setup(hass, fake_board, hass_storage)
    await hub.async_bind_remote(R1, [street])
    options_before = dict(entry.options)
    result = await _open_remote(hass, entry, R1)
    await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remote_remove"})
    await hass.async_block_till_done()
    assert entry.runtime_data is hub
    assert dict(entry.options) == options_before


async def test_ignore_and_unignore_remote(hass, fake_board, hass_storage):
    entry, hub, _, _ = await _setup(hass, fake_board, hass_storage)
    await _hear(hass, fake_board, 0x13B3)
    result = await _open_remote(hass, entry, R2)
    await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remote_ignore"})
    assert R2 in hub.ignored_remotes
    result = await _open_remote(hass, entry, R2)
    assert result["menu_options"] == ["remote_unignore"]
    await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remote_unignore"})
    assert R2 not in hub.ignored_remotes


async def test_remotes_without_any_remote_aborts(hass, fake_board, hass_storage):
    entry, _, _, _ = await _setup(hass, fake_board, hass_storage)
    options = hass.config_entries.options
    result = await options.async_init(entry.entry_id)
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remotes"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_remotes"


async def _fix_flow(hass, issue_id):
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    return manager, await manager.async_init(DOMAIN, context={"issue_id": issue_id})


async def test_new_remote_repair_binds(hass, fake_board, hass_storage):
    _, hub, _, hall = await _setup(hass, fake_board, hass_storage)
    await _hear(hass, fake_board, 0x13B3)
    manager, result = await _fix_flow(hass, f"new_remote_{R2}")
    assert result["type"] is FlowResultType.MENU
    assert result["description_placeholders"]["address"] == "0x13B3"
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "bind"})
    result = await manager.async_configure(result["flow_id"], {"lamps": [hall]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hub.remotes[R2].lamps == [hall]
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None


async def test_new_remote_repair_ignores(hass, fake_board, hass_storage):
    entry, hub, _, _ = await _setup(hass, fake_board, hass_storage)
    await _hear(hass, fake_board, 0x13B3)
    manager, result = await _fix_flow(hass, f"new_remote_{R2}")
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "ignore"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert R2 in hub.ignored_remotes
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _hear(hass, fake_board, 0x13B3, 200)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"new_remote_{R2}") is None


async def test_remote_labels_translated(hass, fake_board, hass_storage):
    """The labels are built by the server, so they follow the server language."""
    hass.config.language = "ru"
    entry, hub, _, _ = await _setup(hass, fake_board, hass_storage)
    await _hear(hass, fake_board, 0x37AF)
    await _hear(hass, fake_board, 0x13B3)
    hub.ignored_remotes.add(R2)
    options = hass.config_entries.options
    result = await options.async_init(entry.entry_id)
    result = await options.async_configure(result["flow_id"], {"next_step_id": "remotes"})
    selector = result["data_schema"].schema["remote"]
    labels = {option["value"]: option["label"] for option in selector.config["options"]}
    assert labels[R1] == "0x37AF: Новый пульт (Philips Valen series)"
    assert labels[R2] == "0x13B3: игнорируется (Philips Valen series)"
