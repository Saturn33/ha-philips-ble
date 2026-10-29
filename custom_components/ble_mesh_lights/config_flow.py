"""Config flow, options and lamp subentries for BLE Mesh Lights."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from . import hub as hub_module
from . import transport
from .const import (
    AFTER_OUTAGE_NOTHING,
    AFTER_OUTAGE_RESTORE,
    CONF_BASE_ADDRESS,
    CONF_BOARDS,
    CONF_COMMAND_INTERVAL,
    CONF_OUTAGE_SENSOR,
    CONF_POWER_CYCLE_GAP,
    DATA_USED_INDICES,
    DEFAULT_BASE_ADDRESS,
    DEFAULT_COMMAND_INTERVAL,
    DEFAULT_POWER_CYCLE_GAP,
    DOMAIN,
    LAMP_ADDRESS,
    LAMP_AFTER_OUTAGE,
    LAMP_ALIASES,
    LAMP_INDEX,
    LAMP_MAC,
    LAMP_PROFILE,
    LAMP_REMOTE,
    SUBENTRY_LAMP,
)
from .hub import Hub, remote_key
from .profile import Profile
from .state import apply, power_restored

TITLE = "BLE Mesh Lights"
BASE_MIN, BASE_MAX = 0x0001, 0x7F00
LAMP_AREA = "area"
NEW_LAMP_TIMEOUT = 120.0
REMOTE_PAIRING_TIMEOUT = 120.0
REMOTE_BUTTON_TIMEOUT = 60.0
TEST_PAUSE = 2.0
UNKNOWN_REMOTE_WINDOW = 1800.0  # unknown remotes heard within this many seconds are listed


def lamp_options(hub: Hub) -> list[selector.SelectOptionDict]:
    entry = hub.entry
    return [
        selector.SelectOptionDict(value=sub_id, label=sub.title)
        for sub_id, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_LAMP
    ]


def lamps_schema(hub: Hub, current: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("lamps", default=current): selector.SelectSelector(
                selector.SelectSelectorConfig(options=lamp_options(hub), multiple=True)
            )
        }
    )


def _parse_base(text: str) -> int | None:
    try:
        value = int(str(text).strip(), 16)
    except ValueError:
        return None
    return value if BASE_MIN <= value <= BASE_MAX else None


def _settings_schema(defaults: dict[str, Any], base_locked: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_BOARDS, default=defaults.get(CONF_BOARDS, [])): selector.DeviceSelector(
            selector.DeviceSelectorConfig(integration="esphome", multiple=True)
        ),
    }
    if not base_locked:
        base = defaults.get(CONF_BASE_ADDRESS, DEFAULT_BASE_ADDRESS)
        text = f"0x{base:04X}" if isinstance(base, int) else str(base)
        fields[vol.Required(CONF_BASE_ADDRESS, default=text)] = selector.TextSelector()
    interval = defaults.get(CONF_COMMAND_INTERVAL, DEFAULT_COMMAND_INTERVAL)
    fields[vol.Required(CONF_COMMAND_INTERVAL, default=interval)] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0.2, max=5, step=0.1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
        )
    )
    gap = defaults.get(CONF_POWER_CYCLE_GAP, DEFAULT_POWER_CYCLE_GAP)
    fields[vol.Required(CONF_POWER_CYCLE_GAP, default=gap)] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1.5, max=9, step=0.5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
        )
    )
    outage = defaults.get(CONF_OUTAGE_SENSOR)
    outage_key = vol.Optional(CONF_OUTAGE_SENSOR, description={"suggested_value": outage} if outage else None)
    fields[outage_key] = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
    return vol.Schema(fields)


async def _validate(
    hass: HomeAssistant, user_input: dict[str, Any], base: int | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Options from the form, or errors."""
    errors: dict[str, str] = {}
    boards = await transport.async_boards(hass, user_input.get(CONF_BOARDS, []))
    if not boards or not all(hass.services.has_service("esphome", board.service) for board in boards):
        errors[CONF_BOARDS] = "no_boards"
    if base is None:
        base = _parse_base(user_input.get(CONF_BASE_ADDRESS, ""))
        if base is None:
            errors[CONF_BASE_ADDRESS] = "invalid_base_address"
    options: dict[str, Any] = {
        CONF_BOARDS: list(user_input.get(CONF_BOARDS, [])),
        CONF_BASE_ADDRESS: base,
        CONF_COMMAND_INTERVAL: float(user_input[CONF_COMMAND_INTERVAL]),
        CONF_POWER_CYCLE_GAP: float(user_input.get(CONF_POWER_CYCLE_GAP, DEFAULT_POWER_CYCLE_GAP)),
    }
    if user_input.get(CONF_OUTAGE_SENSOR):
        options[CONF_OUTAGE_SENSOR] = user_input[CONF_OUTAGE_SENSOR]
    return options, errors


class BleMeshLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = await _validate(self.hass, user_input, None)
            if not errors:
                return self.async_create_entry(title=TITLE, data={DATA_USED_INDICES: []}, options=options)
        return self.async_show_form(
            step_id="user", data_schema=_settings_schema(user_input or {}, False), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BleMeshLightsOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_LAMP: LampSubentryFlow}


class BleMeshLightsOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._remote: str | None = None

    def _hub(self) -> Hub | None:
        entry = self.config_entry
        return entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "remotes"])

    def _remote_choices(self, hub: Hub) -> list[selector.SelectOptionDict]:
        titles = {sub_id: sub.title for sub_id, sub in self.config_entry.subentries.items()}

        def profile_name(key: str) -> str:
            profile = hub.profiles.get(key.split(":")[0])
            return profile.name if profile else key.split(":")[0]

        def address(key: str) -> str:
            return "0x" + key.split(":")[1].upper()

        choices = [
            selector.SelectOptionDict(
                value=key,
                label=f"{address(key)}: {', '.join(titles.get(i, '?') for i in remote.lamps) or '-'}",
            )
            for key, remote in hub.remotes.items()
        ]
        now = hub_module.monotonic()
        for key, heard in hub.unknown_remotes.items():
            if (
                key not in hub.remotes
                and key not in hub.ignored_remotes
                and now - heard < UNKNOWN_REMOTE_WINDOW
            ):
                label = f"{address(key)}: New remote ({profile_name(key)})"
                choices.append(selector.SelectOptionDict(value=key, label=label))
        for key in sorted(hub.ignored_remotes):
            label = f"{address(key)}: ignored ({profile_name(key)})"
            choices.append(selector.SelectOptionDict(value=key, label=label))
        return choices

    async def async_step_remotes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._remote = user_input["remote"]
            return await self.async_step_remote_action()
        choices = self._remote_choices(hub)
        if not choices:
            return self.async_abort(reason="no_remotes")
        schema = vol.Schema(
            {vol.Required("remote"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices))}
        )
        return self.async_show_form(step_id="remotes", data_schema=schema)

    async def async_step_remote_action(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None or self._remote is None:
            return self.async_abort(reason="not_loaded")
        if self._remote in hub.ignored_remotes:
            menu = ["remote_unignore"]
        elif self._remote in hub.remotes:
            menu = ["remote_lamps", "remote_remove"]
        else:
            menu = ["remote_lamps", "remote_ignore"]
        address = "0x" + self._remote.split(":")[1].upper()
        return self.async_show_menu(
            step_id="remote_action", menu_options=menu, description_placeholders={"address": address}
        )

    async def async_step_remote_lamps(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None or self._remote is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            await hub.async_bind_remote(self._remote, list(user_input.get("lamps", [])))
            # Not create_entry: that would replace the options and reload the entry.
            return self.async_abort(reason="remotes_updated")
        current = hub.remotes[self._remote].lamps if self._remote in hub.remotes else []
        return self.async_show_form(step_id="remote_lamps", data_schema=lamps_schema(hub, list(current)))

    async def async_step_remote_remove(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None or self._remote is None:
            return self.async_abort(reason="not_loaded")
        await hub.async_remove_remote(self._remote)
        return self.async_abort(reason="remotes_updated")

    async def async_step_remote_ignore(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None or self._remote is None:
            return self.async_abort(reason="not_loaded")
        hub.async_ignore_remote(self._remote)
        return self.async_abort(reason="remotes_updated")

    async def async_step_remote_unignore(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None or self._remote is None:
            return self.async_abort(reason="not_loaded")
        hub.async_ignore_remote(self._remote, ignore=False)
        return self.async_abort(reason="remotes_updated")

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        base = entry.options.get(CONF_BASE_ADDRESS, DEFAULT_BASE_ADDRESS)
        # Lamps are paired with base + index: moving the base would orphan them.
        locked = bool(entry.data.get(DATA_USED_INDICES))
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = await _validate(self.hass, user_input, base if locked else None)
            if not errors:
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(user_input or dict(entry.options), locked),
            errors=errors,
            description_placeholders={"base_address": f"0x{base:04X}"},
        )


class LampSubentryFlow(ConfigSubentryFlow):
    """Add a lamp: find it by a power cycle, pair HA's address, bind its remote, test."""

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._profile: Profile | None = None
        self._mac: str | None = None
        self._index: int | None = None
        self._address: int | None = None
        self._remote: str | None = None
        self._task: asyncio.Task | None = None
        self._error = ""

    def _hub(self) -> Hub | None:
        entry = self._get_entry()
        return entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    def _placeholders(self) -> dict[str, str]:
        profile = self._profile
        return {
            "name": self._input.get("name", ""),
            "reset_hint": profile.pairing.reset_hint if profile else "",
            "error": self._error,
        }

    async def _progress(self, step_id: str, job, outcomes: dict[type[BaseException], str], done: str):
        """Run `job` as this step's progress task; then go to `done`, or to the step that
        `outcomes` names for the exception it raised."""
        if self._task is None:
            self._task = self.hass.async_create_task(job())
        if not self._task.done():
            return self.async_show_progress(
                step_id=step_id,
                progress_action=step_id,
                progress_task=self._task,
                description_placeholders=self._placeholders(),
            )
        task, self._task = self._task, None
        try:
            task.result()
        except Exception as err:  # noqa: BLE001  (mapped to a step below, else re-raised)
            for kind, step in outcomes.items():
                if isinstance(err, kind):
                    self._error = str(err)
                    return self.async_show_progress_done(next_step_id=step)
            raise
        return self.async_show_progress_done(next_step_id=done)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._input = user_input
            self._profile = hub.profiles[user_input[LAMP_PROFILE]]
            return await self.async_step_power_cycle()
        profiles = [
            selector.SelectOptionDict(value=profile.id, label=profile.name)
            for profile in hub.profiles.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(LAMP_PROFILE, default=profiles[0]["value"] if profiles else vol.UNDEFINED): (
                    selector.SelectSelector(selector.SelectSelectorConfig(options=profiles))
                ),
                vol.Required("name"): selector.TextSelector(),
                vol.Optional(LAMP_AREA): selector.AreaSelector(),
                vol.Optional(LAMP_ALIASES, default=[]): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _detect_and_pair(self) -> None:
        """One task, so the lamp's pairing window right after power-on is not lost waiting for
        the browser: wait for the lamp, then pair at once."""
        hub, profile = self._hub(), self._profile
        if hub is None or profile is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        self._mac = await hub.async_wait_for_new_lamp(profile, NEW_LAMP_TIMEOUT)
        if self._address is None:
            self._index, self._address = hub.allocate_address()
        board, _rssi = hub.choose_board_for(self._mac)
        await hub.async_pair(profile, self._address, board)

    async def async_step_power_cycle(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "power_cycle",
            self._detect_and_pair,
            {
                TimeoutError: "power_cycle_timeout",
                HomeAssistantError: "send_failed",
                ValueError: "no_address_left",
            },
            "blinked",
        )

    async def async_step_power_cycle_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return await self.async_step_power_cycle()
        return self.async_show_form(
            step_id="power_cycle_timeout",
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_send_failed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="send_failed", description_placeholders=self._placeholders())

    async def async_step_no_address_left(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_abort(reason="no_address_left", description_placeholders=self._placeholders())

    async def async_step_blinked(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="blinked",
            menu_options=["remote", "power_cycle"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_remote(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="remote",
            menu_options=["remote_wait", "test"],
            description_placeholders=self._placeholders(),
        )

    async def _wait_for_remote_pairing(self) -> None:
        hub, profile = self._hub(), self._profile
        if hub is None or profile is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        source = await hub.async_wait_for_pairing(profile, REMOTE_PAIRING_TIMEOUT)
        self._remote = remote_key(profile.id, source)

    async def async_step_remote_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "remote_wait", self._wait_for_remote_pairing, {TimeoutError: "remote"}, "remote_button"
        )

    async def _wait_for_remote_button(self) -> None:
        hub = self._hub()
        if hub is None or self._remote is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        await hub.async_wait_for_press(self._remote, REMOTE_BUTTON_TIMEOUT)

    async def async_step_remote_button(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        # Without a press the remote leaves pairing mode on its own later; test anyway.
        return await self._progress(
            "remote_button", self._wait_for_remote_button, {TimeoutError: "test"}, "test"
        )

    async def _switch_off_and_on(self) -> None:
        hub, profile = self._hub(), self._profile
        if hub is None or profile is None or self._mac is None or self._address is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        board, _rssi = hub.choose_board_for(self._mac)
        await hub.async_command_address(profile, self._address, "off", board)
        await hub_module.sleep(TEST_PAUSE)  # long enough to see the light off
        await hub.async_command_address(profile, self._address, "on", board)

    async def async_step_test(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "test", self._switch_off_and_on, {HomeAssistantError: "test_failed"}, "test_result"
        )

    async def async_step_test_result(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="test_result",
            menu_options=["finish", "test_failed"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_test_failed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="test_failed",
            menu_options=["test", "power_cycle", "reset_abort"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_reset_abort(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="reset_needed", description_placeholders=self._placeholders())

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        hub, profile = self._hub(), self._profile
        if hub is None:
            return self.async_abort(reason="not_loaded")
        assert profile is not None and self._mac is not None
        # Just powered on, then switched off and on by the test.
        await hub.async_seed_lamp_state(
            self._mac, apply(profile, apply(profile, power_restored(profile), "off"), "on")
        )
        data = {
            LAMP_PROFILE: profile.id,
            LAMP_MAC: self._mac,
            LAMP_ADDRESS: self._address,
            LAMP_INDEX: self._index,
            LAMP_AFTER_OUTAGE: AFTER_OUTAGE_NOTHING,
            LAMP_ALIASES: [alias.strip() for alias in self._input.get(LAMP_ALIASES, []) if alias.strip()],
            LAMP_REMOTE: self._remote,
        }
        if self._input.get(LAMP_AREA):
            data[LAMP_AREA] = self._input[LAMP_AREA]
        return self.async_create_entry(title=self._input["name"], data=data, unique_id=self._mac)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = dict(subentry.data)
            if LAMP_AFTER_OUTAGE in user_input:
                data[LAMP_AFTER_OUTAGE] = user_input[LAMP_AFTER_OUTAGE]
            return self.async_update_and_abort(entry, subentry, title=user_input["name"], data=data)
        fields: dict[Any, Any] = {vol.Required("name", default=subentry.title): selector.TextSelector()}
        if entry.options.get(CONF_OUTAGE_SENSOR):
            fields[
                vol.Required(
                    LAMP_AFTER_OUTAGE, default=subentry.data.get(LAMP_AFTER_OUTAGE, AFTER_OUTAGE_NOTHING)
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[AFTER_OUTAGE_NOTHING, AFTER_OUTAGE_RESTORE], translation_key="after_outage"
                )
            )
        hub = self._hub()
        profile = hub.profiles.get(subentry.data[LAMP_PROFILE]) if hub else None
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(fields),
            description_placeholders={"reset_hint": profile.pairing.reset_hint if profile else ""},
        )
