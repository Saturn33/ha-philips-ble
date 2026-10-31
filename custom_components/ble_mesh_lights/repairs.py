"""Repair flows for BLE Mesh Lights."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.storage import Store

from .config_flow import lamps_schema
from .const import LAMP_ADDRESS, STORAGE_SEQUENCE, STORAGE_VERSION, SUBENTRY_LAMP
from .hub import Hub, HubClosed
from .sequence import VERSION as SEQUENCE_VERSION

# Above any counter a lamp has seen in normal use, including earlier skips forward.
RESTART_SEQUENCE = 0x800000


class SequenceStoreFlow(RepairsFlow):
    """The counters are lost while lamps are paired: restart them far above any earlier value."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="not_loaded")
        addresses = {
            f"0x{sub.data[LAMP_ADDRESS]:04x}": RESTART_SEQUENCE
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_LAMP
        }
        store: Store[dict[str, Any]] = Store(self.hass, STORAGE_VERSION, STORAGE_SEQUENCE)
        await store.async_save({"version": SEQUENCE_VERSION, "addresses": addresses})
        await self.hass.config_entries.async_reload(self._entry_id)
        return self.async_create_entry(data={})


class NewRemoteFlow(RepairsFlow):
    """A remote heard for the first time: bind it to lamps or ignore it."""

    def __init__(self, entry_id: str, key: str) -> None:
        self._entry_id = entry_id
        self._key = key

    def _hub(self) -> Hub | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return None
        return entry.runtime_data

    def _placeholders(self, hub: Hub) -> dict[str, str]:
        profile_id, source = self._key.split(":")
        profile = hub.profiles.get(profile_id)
        return {"profile": profile.name if profile else profile_id, "address": f"0x{source.upper()}"}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init", menu_options=["bind", "ignore"], description_placeholders=self._placeholders(hub)
        )

    async def async_step_bind(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            try:
                await hub.async_bind_remote(self._key, list(user_input.get("lamps", [])))
            except HubClosed:
                return self.async_abort(reason="not_loaded")
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="bind",
            data_schema=lamps_schema(hub, []),
            description_placeholders=self._placeholders(hub),
        )

    async def async_step_ignore(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        hub.async_ignore_remote(self._key)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    data = data or {}
    if issue_id == "sequence_store_missing":
        return SequenceStoreFlow(str(data["entry_id"]))
    if issue_id.startswith("new_remote_"):
        return NewRemoteFlow(str(data["entry_id"]), str(data["key"]))
    raise ValueError(f"No fix flow for {issue_id}")
