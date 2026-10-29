"""What HA believes about a lamp, and how each command changes it.

The lamp reports nothing, so this is a model driven by the profile's command
effects. ``None`` in any field means unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .profile import Profile


@dataclass(frozen=True)
class LampState:
    on: bool | None
    color_temp: int | None  # kelvin
    level: int | None  # percent
    scene: str | None = None


UNKNOWN = LampState(None, None, None)


def apply(profile: Profile, state: LampState, command: str) -> LampState:
    effect = profile.commands[command].effect
    if effect is None:
        return state
    if effect.color_temp is not None and effect.level is not None:
        return LampState(True, effect.color_temp, effect.level, effect.scene)
    if effect.power == "on":
        return replace(state, on=True)
    if effect.power == "off":
        return replace(state, on=False)
    if effect.power == "toggle":
        return replace(state, on=None if state.on is None else not state.on)
    if state.on is False:  # an off lamp ignores SELECT and dimming
        return state
    if state.on is None:
        # The lamp may be off and ignore the command: whatever it would change is now unknown.
        return replace(
            state,
            color_temp=None if effect.color_temp_step else state.color_temp,
            level=None if (effect.brightness_step or effect.level is not None) else state.level,
            scene=None,
        )
    result = replace(state, scene=None)
    if effect.color_temp_step:
        color_temp = _step(profile.color_temps, state.color_temp, effect.color_temp_step)
        result = replace(result, color_temp=color_temp)
    if effect.brightness_step:
        result = replace(result, level=_brighter(profile, state.level, effect.brightness_step))
    if effect.level is not None:
        result = replace(result, level=effect.level)
    return result


def _step(values: tuple[int, ...], current: int | None, step: int) -> int | None:
    if current is None:
        return None
    return values[(values.index(current) + step) % len(values)]


def _brighter(profile: Profile, level: int | None, step: int) -> int | None:
    if level is None:
        return None
    levels = profile.brightness_levels  # brightest first
    index = min(max(levels.index(level) - step, 0), len(levels) - 1)
    return levels[index]


def power_restored(profile: Profile) -> LampState:
    effect = profile.power_on
    return LampState(effect.power == "on", effect.color_temp, effect.level)


def forget(state: LampState, *, level: bool = False, color_temp: bool = False) -> LampState:
    return replace(
        state,
        level=None if level else state.level,
        color_temp=None if color_temp else state.color_temp,
        scene=None if (level or color_temp) else state.scene,
    )


def apply_press(profile: Profile, state: LampState, command: str, missed: int) -> tuple[LampState, bool]:
    """A caught remote press after `missed` uncaught ones (spec §7)."""
    if missed > 0:
        effect = profile.commands[command].effect
        sets_temperature = effect is not None and effect.color_temp is not None
        state = forget(state, level=True, color_temp=not sets_temperature)
    result = apply(profile, state, command)
    return result, None in (result.on, result.color_temp, result.level)


def nearest_level(profile: Profile, percent: float) -> int:
    return min(profile.brightness_levels, key=lambda level: abs(level - percent))


def nearest_color_temp(profile: Profile, kelvin: float) -> int:
    """Nearest in mireds, as HA's colour temperature slider is; ties go to the warmer value."""
    target = 1_000_000 / kelvin
    return min(profile.color_temps, key=lambda k: (abs(1_000_000 / k - target), k))


def _snap(values: tuple[int, ...], requested: float, current: int | None, nearest: int) -> int:
    """The nearest step; but when that is the current step and a different value was asked, the
    next step in that direction (a slider moved a little would otherwise change nothing, and HA
    leaves it where it was dropped). None beyond the ends."""
    if current not in values or nearest != current or requested == current:
        return nearest
    ordered = sorted(values)
    index = ordered.index(current) + (1 if requested > current else -1)
    return ordered[index] if 0 <= index < len(ordered) else current


def snap_level(profile: Profile, percent: float, current: int | None) -> int:
    levels = profile.brightness_levels
    distance = min(abs(level - percent) for level in levels)
    tied = [level for level in levels if abs(level - percent) == distance]
    # Halfway between two steps: the one nearer the current step (the smaller change).
    nearest = min(tied, key=lambda level: abs(level - current)) if current in levels else tied[0]
    return _snap(levels, percent, current, nearest)


def snap_color_temp(profile: Profile, kelvin: float, current: int | None) -> int:
    return _snap(profile.color_temps, kelvin, current, nearest_color_temp(profile, kelvin))
