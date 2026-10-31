"""Config flow, options and lamp subentries for BLE Mesh Lights."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any, TypeVar

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector, translation

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
    TRANSMIT_REPEAT,
)
from .hub import Hub, HubClosed, NoAddressLeft
from .profile import Profile
from .state import apply, power_restored

TITLE = "BLE Mesh Lights"
BASE_MIN, BASE_MAX = 0x0001, 0x7F00
LAMP_AREA = "area"
NEW_LAMP_TIMEOUT = 120.0
REMOTE_BUTTON_TIMEOUT = 60.0
TEST_PAUSE = 2.0
RELOAD_WAIT = 30.0  # s the wizard waits for a reloading entry to come back
RELOAD_POLL = 0.5

T = TypeVar("T")
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

    async def _remote_choices(self, hub: Hub) -> list[selector.SelectOptionDict]:
        # Built by the server, so in the server language: an options flow does not know its user
        # either (helpers/data_entry_flow.py, get_context). Keys: selector.remote_label.options.*.
        texts = await translation.async_get_translations(
            self.hass, self.hass.config.language, "selector", [DOMAIN]
        )

        def label_of(kind: str, **values: str) -> str:
            text = texts.get(f"component.{DOMAIN}.selector.remote_label.options.{kind}", kind)
            return text.format(**values)

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
                label = f"{address(key)}: {label_of('new', profile=profile_name(key))}"
                choices.append(selector.SelectOptionDict(value=key, label=label))
        for key in sorted(hub.ignored_remotes):
            label = f"{address(key)}: {label_of('ignored', profile=profile_name(key))}"
            choices.append(selector.SelectOptionDict(value=key, label=label))
        return choices

    async def async_step_remotes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        hub = self._hub()
        if hub is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._remote = user_input["remote"]
            return await self.async_step_remote_action()
        choices = await self._remote_choices(hub)
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
            try:
                await hub.async_bind_remote(self._remote, list(user_input.get("lamps", [])))
            except HubClosed:
                return self.async_abort(reason="not_loaded")
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


# Steps whose text holds a profile's hint (factory reset, remote pairing). They are shown as
# `<step>_<profile.texts>`, a variant written out in the translations, so the frontend shows the
# hint in the user's language (a flow does not know its user: helpers/data_entry_flow.py,
# get_context; and a custom integration cannot reference another translation key).
VARIANT_STEPS = frozenset({"remote_pair", "pair_again", "settings", "test_failed"})


class LampSubentryFlow(ConfigSubentryFlow):
    """Add a lamp: find it by a power cycle, pair HA's address, test, save; then optionally bind
    the remote the user presses. Reconfigure: the lamp's settings, or pair it again."""

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._profile: Profile | None = None
        self._mac: str | None = None
        self._index: int | None = None
        self._address: int | None = None
        self._sub_id: str | None = None  # the saved lamp (after the test, or the reconfigured one)
        self._again = False  # pairing an existing lamp again
        self._heard: tuple[str, str] | None = None  # (remote key, command) of the caught press
        self._heard_bound = False  # the remote already controlled this lamp when it was caught
        self._task: asyncio.Task | None = None
        self._error = ""

    def _hub(self) -> Hub | None:
        entry = self._get_entry()
        return entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    async def _with_hub(self, job: Callable[[Hub], Awaitable[T]]) -> T:
        """Run `job` with the entry's current hub. If the entry reloads meanwhile (options saved,
        another lamp added, this lamp saved), the old hub refuses with HubClosed: wait for the new
        one and run the job again with it, never with the old hub (its counters are stale). Once
        the lamp is saved, only a hub that has it counts."""
        while True:
            deadline = hub_module.monotonic() + RELOAD_WAIT
            while (hub := self._hub()) is None or (
                self._sub_id is not None and self._sub_id not in hub.lamps
            ):
                if hub_module.monotonic() > deadline:
                    raise HomeAssistantError("BLE Mesh Lights is not loaded")
                await hub_module.sleep(RELOAD_POLL)
            try:
                return await job(hub)
            except HubClosed:
                await hub_module.sleep(RELOAD_POLL)

    def __getattr__(self, name: str) -> Any:
        """A submitted variant step (`async_step_remote_pair_valen`) goes to its base handler. HA
        looks a step up with getattr/hasattr (data_entry_flow.py, _async_handle_step and
        _raise_if_step_does_not_exist), so anything else must stay an AttributeError."""
        profile = self.__dict__.get("_profile")  # never self._profile: no recursion before __init__
        if profile is not None and name.startswith("async_step_") and name.endswith(f"_{profile.texts}"):
            base = name.removeprefix("async_step_").removesuffix(f"_{profile.texts}")
            if base in VARIANT_STEPS:
                return getattr(self, f"async_step_{base}")
        raise AttributeError(name)

    def _variant(self, base: str) -> str:
        """The step id to show: the profile's variant, or the base (settings of a lamp without a profile)."""
        return f"{base}_{self._profile.texts}" if self._profile is not None else base

    def _placeholders(self) -> dict[str, str]:
        return {"name": self._input.get("name", ""), "error": self._error}

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
        # Not the last step: the button reads Next (data_entry_flow.py, async_show_form last_step).
        return self.async_show_form(step_id="user", data_schema=schema, last_step=False)

    async def _detect_and_pair(self) -> None:
        """One task, so the lamp's pairing window right after power-on is not lost waiting for
        the browser: wait for the lamp, then pair at once. Every send goes through the current hub."""
        profile = self._profile
        if profile is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        mac = self._mac if self._again else None
        self._mac = await self._with_hub(
            lambda hub: hub.async_wait_for_new_lamp(profile, NEW_LAMP_TIMEOUT, mac=mac)
        )
        if self._address is None:

            async def allocate(hub: Hub) -> tuple[int, int]:
                return hub.allocate_address()

            self._index, self._address = await self._with_hub(allocate)
        rules = profile.pairing
        for request in range(rules.requests):
            if request:
                await hub_module.sleep(rules.interval)
            await self._send(rules.command, repeat=1)

    async def _send(self, command: str, repeat: int = TRANSMIT_REPEAT) -> None:
        """One command as the new lamp's address, through the current hub."""
        profile, mac, address = self._profile, self._mac, self._address
        assert profile is not None and mac is not None and address is not None

        async def send(hub: Hub) -> bool:
            board, _rssi = hub.choose_board_for(mac)
            return await hub.async_transmit(profile, address, command, board, repeat=repeat)

        await self._with_hub(send)

    async def async_step_power_cycle(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "power_cycle",
            self._detect_and_pair,
            {
                TimeoutError: "power_cycle_timeout",
                NoAddressLeft: "no_address_left",
                # Anything else that stops a send (a counter error, an address a remote uses).
                HomeAssistantError: "send_failed",
                ValueError: "send_failed",
            },
            # A light blinks only when it stores a new sender: one that still knows its address
            # (pairing again without a reset) does not, so there only the test tells.
            "again_test" if self._again else "blinked",
        )

    async def async_step_power_cycle_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return await self.async_step_power_cycle()
        return self.async_show_form(
            step_id="power_cycle_timeout",
            last_step=False,
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_not_loaded_abort(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """The entry went away while a step waited for the remote (nothing was being sent)."""
        return self.async_abort(reason="not_loaded")

    async def async_step_send_failed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="send_failed", description_placeholders=self._placeholders())

    async def async_step_no_address_left(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_abort(reason="no_address_left", description_placeholders=self._placeholders())

    async def async_step_blinked(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="blinked",
            menu_options=["test", "power_cycle"],
            description_placeholders=self._placeholders(),
        )

    async def _switch_off_and_on(self) -> None:
        if self._profile is None or self._mac is None or self._address is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")
        await self._send("off")
        await hub_module.sleep(TEST_PAUSE)  # long enough to see the light off
        await self._send("on")

    async def async_step_test(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "test", self._switch_off_and_on, {HomeAssistantError: "test_failed"}, "test_result"
        )

    async def async_step_again_test(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The test of "Pair again", with a text that the blink may or may not come."""
        return await self._progress(
            "again_test", self._switch_off_and_on, {HomeAssistantError: "test_failed"}, "test_result"
        )

    async def async_step_test_result(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="test_result",
            menu_options=["save", "test_failed"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_test_failed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id=self._variant("test_failed"),
            menu_options=["test", "power_cycle", "reset_abort"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_reset_abort(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        reason = "reset_needed_again" if self._again else "reset_needed"
        return self.async_abort(reason=self._variant(reason), description_placeholders=self._placeholders())

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The test passed: save a new lamp now, so closing the dialog later keeps it."""
        if self._again:
            return await self.async_step_remote()
        profile = self._profile
        assert profile is not None and self._mac is not None
        # Just powered on, then off and on by the test.
        state = power_restored(profile)
        for command in ("off", "on"):
            state = apply(profile, state, command)
        data = {
            LAMP_PROFILE: profile.id,
            LAMP_MAC: self._mac,
            LAMP_ADDRESS: self._address,
            LAMP_INDEX: self._index,
            LAMP_AFTER_OUTAGE: AFTER_OUTAGE_NOTHING,
            LAMP_ALIASES: [alias.strip() for alias in self._input.get(LAMP_ALIASES, []) if alias.strip()],
            LAMP_REMOTE: None,
        }
        if self._input.get(LAMP_AREA):
            data[LAMP_AREA] = self._input[LAMP_AREA]
        subentry = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=SUBENTRY_LAMP,
            title=self._input["name"],
            unique_id=self._mac,
        )
        # A subentry flow cannot chain another flow after it ends (config_entries.py,
        # _async_set_next_flow_if_valid: "next_flow only supports FlowType.CONFIG_FLOW"), so the
        # lamp is added here (ConfigEntries.async_add_subentry) and the flow goes on to the remote.
        # The entry reloads for the new lamp; _with_hub waits for a hub that has it.
        mac = self._mac

        async def seed_and_add(hub: Hub) -> None:
            # One job on a running hub: added during a reload's setup, the lamp would be missed by
            # the new hub (Hub.async_setup reads entry.subentries at its start, and the entry's
            # update listener is registered only after setup, __init__.py).
            await hub.async_seed_lamp_state(mac, state)
            if hub.closed or self._hub() is not hub:
                raise HubClosed("BLE Mesh Lights reloaded")
            self.hass.config_entries.async_add_subentry(self._get_entry(), subentry)

        try:
            await self._with_hub(seed_and_add)
        except HomeAssistantError:
            return self.async_abort(reason="not_loaded")
        self._sub_id = subentry.subentry_id
        return await self.async_step_remote()

    async def async_step_remote(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="remote",
            menu_options=["remote_pair", "done"],
            description_placeholders=self._placeholders(),
        )

    async def async_step_remote_pair(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            return await self.async_step_remote_press()
        return self.async_show_form(
            step_id=self._variant("remote_pair"),
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
            last_step=False,
        )

    async def _wait_for_remote_press(self) -> None:
        profile = self._profile
        if profile is None:
            raise HomeAssistantError("BLE Mesh Lights is not loaded")

        async def wait(hub: Hub) -> tuple[str, str]:
            heard = await hub.async_wait_for_remote_press(profile, REMOTE_BUTTON_TIMEOUT)
            # Did the remote already control this lamp when the press came? Then the press was
            # applied as it came, and later presses too: Bind must not put the scene back.
            remote = hub.remotes.get(heard[0])
            self._heard_bound = remote is not None and self._sub_id in remote.lamps
            return heard

        self._heard = await self._with_hub(wait)

    async def async_step_remote_press(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._progress(
            "remote_press",
            self._wait_for_remote_press,
            {TimeoutError: "remote_not_heard", HomeAssistantError: "not_loaded_abort"},
            "remote_heard",
        )

    async def async_step_remote_not_heard(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="remote_not_heard",
            menu_options=["remote_pair", "done"],
            description_placeholders=self._placeholders(),
        )

    async def _heard_placeholders(self) -> dict[str, str]:
        assert self._heard is not None
        key, command = self._heard
        names = await translation.async_get_translations(
            self.hass, self.hass.config.language, "entity", [DOMAIN]
        )
        button = names.get(
            f"component.{DOMAIN}.entity.event.button_press.state_attributes.event_type.state.{command}",
            command,
        )
        hub = self._hub()
        lamps = hub.remote_lamp_names(key) if hub else []
        return {
            **self._placeholders(),
            "address": f"0x{int(key.split(':')[1], 16):04X}",
            "button": button,
            "lamps": ", ".join(lamps) or "—",
        }

    async def async_step_remote_heard(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="remote_heard",
            menu_options=["remote_bind", "remote_press", "done"],
            description_placeholders=await self._heard_placeholders(),
        )

    async def async_step_remote_bind(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        assert self._heard is not None and self._sub_id is not None
        key, command = self._heard
        sub_id = self._sub_id
        placeholders = await self._heard_placeholders()

        async def bind(hub: Hub) -> None:
            await hub.async_add_remote_lamp(key, sub_id)
            lamp = hub.lamps[sub_id]
            effect = lamp.profile.commands[command].effect
            # A scene sets the whole state; the light obeyed it (the remote's pairing mode had
            # ended), so HA follows. Any other button only binds.
            scene = effect is not None and effect.color_temp is not None and effect.level is not None
            if scene and not self._heard_bound:
                hub.set_assumed_state(sub_id, apply(lamp.profile, lamp.state, command))

        try:
            await self._with_hub(bind)
        except HomeAssistantError:
            return self.async_abort(reason="not_loaded")
        return self.async_abort(reason="remote_bound", description_placeholders=placeholders)

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        reason = "paired_again" if self._again else "lamp_added"
        return self.async_abort(reason=reason, description_placeholders=self._placeholders())

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._input = {"name": self._get_reconfigure_subentry().title}
        self._find_profile()
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["settings", "pair_again"],
            description_placeholders=self._placeholders(),
        )

    def _find_profile(self) -> None:
        """The reconfigured lamp's profile, looked up again on each screen: the menu may have been
        opened while the entry was reloading (no hub yet)."""
        hub = self._hub()
        if hub is not None:
            self._profile = hub.profiles.get(self._get_reconfigure_subentry().data[LAMP_PROFILE])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is None:
            self._find_profile()
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
        return self.async_show_form(
            step_id=self._variant("settings"),
            data_schema=vol.Schema(fields),
            description_placeholders=self._placeholders(),
        )

    async def async_step_pair_again(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """After a factory reset: pair the lamp's own address again, keeping the lamp."""
        if self._hub() is None:
            return self.async_abort(reason="not_loaded")
        if user_input is None:
            self._find_profile()
        if self._profile is None:
            return self.async_abort(reason="profile_failed")
        if user_input is None:
            return self.async_show_form(
                step_id=self._variant("pair_again"),
                last_step=False,
                data_schema=vol.Schema({}),
                description_placeholders=self._placeholders(),
            )
        subentry = self._get_reconfigure_subentry()
        self._again = True
        self._sub_id = subentry.subentry_id
        self._mac = subentry.data[LAMP_MAC]
        self._address = subentry.data[LAMP_ADDRESS]
        self._index = subentry.data[LAMP_INDEX]
        return await self.async_step_power_cycle()
