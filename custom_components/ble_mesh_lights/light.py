"""The lamp as a light: stepped brightness, a few colour temperatures, scenes as effects."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, ServiceResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import BleMeshLightsConfigEntry
from . import hub as hub_module
from .const import DOMAIN, LAMP_ALIASES
from .entity import LampEntity
from .planner import Target, plan
from .state import LampState, nearest_color_temp, nearest_level, scene_of, snap_color_temp, snap_level
from .transport import BoardUnavailable

_LOGGER = logging.getLogger(__name__)

ALIASES_APPLIED = "aliases_applied"
FAVORITES_APPLIED = "favorites_applied"
# HA's colour temperature slider calls turn_on every 0.5 s while it is dragged: calls closer than
# this belong to one drag.
DRAG_WINDOW = 2.0
# The state is published again this many seconds after the last request: the slider may still be
# held when the request is answered, and it re-reads the state only on an update after release.
REPUBLISH_AFTER = (1.0, 3.0, 6.0)


async def async_setup_entry(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    for subentry_id in hub.lamps:
        async_add_entities([BleMeshLight(hub, subentry_id)], config_subentry_id=subentry_id)


class BleMeshLight(LampEntity, LightEntity):
    _attr_name = None
    _attr_translation_key = "lamp"
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_supported_features = LightEntityFeature.EFFECT
    # Changes on every publish (see _publish); of no use in the history.
    _unrecorded_attributes = frozenset({"published_at"})

    def __init__(self, hub, subentry_id: str) -> None:
        super().__init__(hub, subentry_id)
        profile = self.lamp.profile
        self._attr_min_color_temp_kelvin = min(profile.color_temps)
        self._attr_max_color_temp_kelvin = max(profile.color_temps)
        self._attr_effect_list = list(profile.scenes)
        self._published_at: str | None = None
        self._drag: dict[str, tuple[float, int | None]] = {}  # value → (last call, value at drag start)
        self._last_target: tuple[float, Target] | None = None
        self._republish: list[Callable[[], None]] = []

    def _drag_start(self, name: str, current: int | None, now: float) -> int | None:
        """The value before the drag began: the direction of a change counts from there, not from
        the step the lamp has reached during the drag (that made the slider ping-pong)."""
        last = self._drag.get(name)
        start = last[1] if last is not None and now - last[0] < DRAG_WINDOW else current
        self._drag[name] = (now, start)
        return start

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_republish)
        self._apply_once()

    def _apply_once(self) -> None:
        """Once per lamp, then HA owns them: the wizard's aliases, and the lamp's colour
        temperatures as favourite colours (HA's own default is 4 values that miss the lamp's steps)."""
        entry = self.hub.entry
        subentry = entry.subentries[self.subentry_id]
        registry = er.async_get(self.hass)
        done: dict[str, bool] = {}
        if not subentry.data.get(ALIASES_APPLIED):
            if aliases := subentry.data.get(LAMP_ALIASES) or []:
                registry.async_update_entity(self.entity_id, aliases=[er.COMPUTED_NAME, *aliases])
            done[ALIASES_APPLIED] = True
        if not subentry.data.get(FAVORITES_APPLIED):
            entity = registry.async_get(self.entity_id)
            options = dict(entity.options.get("light", {})) if entity else {}
            options["favorite_colors"] = [{"color_temp_kelvin": k} for k in self.lamp.profile.color_temps]
            registry.async_update_entity_options(self.entity_id, "light", options)
            done[FAVORITES_APPLIED] = True
        if done:
            self.hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, **done})

    @property
    def is_on(self) -> bool | None:
        return self.lamp.state.on

    @property
    def brightness(self) -> int | None:
        level = self.lamp.state.level
        return None if level is None else round(level * 255 / 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        return self.lamp.state.color_temp

    @property
    def effect(self) -> str | None:
        return scene_of(self.lamp.profile, self.lamp.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"state_uncertain": self.lamp.uncertain, "published_at": self._published_at}

    async def async_turn_on(self, **kwargs: Any) -> None:
        profile, state = self.lamp.profile, self.lamp.state
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            if effect not in profile.scenes:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={"effect": effect, "effects": ", ".join(profile.scenes)},
                )
            await self.hub.async_command(self.subentry_id, effect)
            return
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        if brightness is None and kelvin is None:
            await self.hub.async_power(self.subentry_id, True)
            return
        # In the direction of the change, so a slider moved a little always lands on a new step
        # (HA leaves the slider where it was dropped if the state does not change).
        now = hub_module.monotonic()
        color_temp = level = None
        if kelvin is not None:
            color_temp = snap_color_temp(
                profile, kelvin, self._drag_start("color_temp", state.color_temp, now)
            )
        if brightness is not None:
            percent = round(brightness * 100 / 255)
            level = snap_level(profile, percent, self._drag_start("level", state.level, now))
        target = Target(True, color_temp, level)
        last = self._last_target
        self._last_target = (now, target)
        # The same step again during a drag is skipped while the chain to it runs or the lamp is
        # already there; not if something else (a remote, a scene) has moved the lamp meanwhile.
        skip = (
            last is not None
            and last[1] == target
            and now - last[0] < DRAG_WINDOW
            and (self.hub.chain_running(self.subentry_id) or not plan(profile, state, target))
        )
        _LOGGER.debug(
            "%s: turn_on %s, lamp at %s, drag start %s → %s%s",
            self.entity_id,
            {k: v for k, v in kwargs.items() if k in (ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN)},
            state,
            {name: start for name, (_, start) in self._drag.items()},
            target,
            " (same step again: skipped)" if skip else "",
        )
        try:
            if skip:
                return  # the same step again during a drag: let the running chain finish
            await self.hub.async_set_target(self.subentry_id, target)
        except Exception:
            self._last_target = None  # a retry of the same step must not be skipped
            raise
        finally:
            self._publish()
            self._cancel_republish()
            self._republish = [
                async_call_later(self.hass, delay, self._publish_later) for delay in REPUBLISH_AFTER
            ]

    @callback
    def _cancel_republish(self) -> None:
        for cancel in self._republish:
            cancel()
        self._republish = []

    @callback
    def _publish_later(self, _now: datetime) -> None:
        self._publish()

    def _publish(self) -> None:
        """Write the state even if nothing changed. HA's slider ignores updates while it is being
        dragged and re-reads the state only on the next one: without this, a drag whose last
        request changed nothing leaves the slider where it was dropped, between two steps.
        A new published_at makes the update, not a forced write: that would reset the light's
        "last changed" each time."""
        self._published_at = dt_util.utcnow().isoformat()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_power(self.subentry_id, False)

    async def async_set_assumed_state(
        self, on: bool | None = None, color_temp_kelvin: int | None = None, brightness: int | None = None
    ) -> None:
        """What the lamp looks like now, without sending anything; unspecified values stay."""
        profile, state = self.lamp.profile, self.lamp.state
        self.hub.set_assumed_state(
            self.subentry_id,
            LampState(
                state.on if on is None else on,
                state.color_temp
                if color_temp_kelvin is None
                else nearest_color_temp(profile, color_temp_kelvin),
                state.level if brightness is None else nearest_level(profile, brightness * 100 / 255),
            ),
        )

    async def async_send_command(self, command: str, queue: bool = False) -> ServiceResponse:
        """One profile command. With `queue` (the remote card) the press waits its turn and is
        always sent, and the answer says whether it went out instead of raising: one entity's
        exception would fail the whole multi-lamp call and drop the others' answers
        (helpers/service.py, entity_service_call: the first exception is re-raised)."""
        profile = self.lamp.profile
        if command not in profile.commands or command == profile.pairing.command:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_command",
                translation_placeholders={"command": command, "commands": ", ".join(self._commands())},
            )
        if not queue:
            await self.hub.async_command(self.subentry_id, command)
            return None
        try:
            await self.hub.async_press(self.subentry_id, command)
        except hub_module.PressQueueFull as err:
            reason, error = "queue_full", err
        except BoardUnavailable as err:
            reason, error = "no_board", err
        except hub_module.HubClosed as err:
            reason, error = "reloaded", err
        except HomeAssistantError as err:
            reason, error = "failed", err
        except Exception as err:  # noqa: BLE001  (answered, not raised: see above)
            _LOGGER.exception("%s: %s failed", self.entity_id, command)
            reason, error = "failed", err
        else:
            return {"sent": True}
        _LOGGER.warning("%s: %s not sent (%s): %s", self.entity_id, command, reason, error)
        return {"sent": False, "reason": reason, "error": str(error)}

    def _commands(self) -> list[str]:
        profile = self.lamp.profile
        return [name for name in profile.commands if name != profile.pairing.command]

    async def async_skip_sequence(self, amount: int) -> None:
        await self.hub.counters.async_skip(self.lamp.address, amount)
