"""Shortest chain of commands from what HA believes about a lamp to what is wanted.

Breadth-first search over lamp states with the profile's command effects. Within
each search layer, commands that set the whole state (scenes) come first, so among
equally short chains the one that starts from a known state wins: it also repairs
whatever HA got wrong about the lamp.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from .profile import Command, Profile
from .state import LampState, apply


class PlanError(ValueError):
    """The target cannot be reached with this profile."""


@dataclass(frozen=True)
class Target:
    on: bool
    color_temp: int | None = None  # None = keep the current value
    level: int | None = None  # None = keep the current value


def _rank(command: Command) -> int:
    effect = command.effect
    if effect.color_temp is not None and effect.level is not None:
        return 0
    if effect.color_temp_step:
        return 1
    if effect.brightness_step:
        return 2
    return 3


def _ordered_commands(profile: Profile) -> list[str]:
    usable = [c for c in profile.commands.values() if c.effect is not None]
    return [c.name for c in sorted(usable, key=_rank)]  # sorted() is stable: profile order within a rank


def power_command(profile: Profile, on: bool) -> str | None:
    """The profile's explicit on (or off) command, which is safe to send whatever the state; None if
    the profile only has a toggle."""
    wanted = "on" if on else "off"
    for command in profile.commands.values():
        if command.effect is not None and command.effect.power == wanted and command.effect.level is None:
            return command.name
    return None


def _power_command(profile: Profile, state: LampState, on: bool) -> list[str] | None:
    """The power command alone, for a plain on/off; None if the profile cannot do it directly."""
    if state.on is on:
        return []
    explicit = power_command(profile, on)
    if explicit is not None:
        return [explicit]
    if state.on is not None:
        for command in profile.commands.values():
            if command.effect is not None and command.effect.power == "toggle":
                return [command.name]
    return None


def _satisfies(lamp: LampState, target: Target) -> bool:
    if lamp.on is not target.on:
        return False
    if not target.on:
        return True
    return all(
        wanted is None or (actual is not None and actual == wanted)
        for wanted, actual in ((target.color_temp, lamp.color_temp), (target.level, lamp.level))
    )


def plan(profile: Profile, state: LampState, target: Target, max_length: int = 12) -> list[str]:
    if target.level is not None and target.level not in profile.brightness_levels:
        raise PlanError(f"Brightness {target.level}% is not one of {list(profile.brightness_levels)}")
    if target.color_temp is not None and target.color_temp not in profile.color_temps:
        raise PlanError(f"Colour temperature {target.color_temp} K is not one of {list(profile.color_temps)}")

    # A plain on/off (no brightness or temperature asked) is the power command alone: decided
    # from what the caller asked, before filling the target from the state, so that unknown
    # values never turn a plain "on" into a scene.
    if not target.on or (target.color_temp is None and target.level is None):
        direct = _power_command(profile, state, target.on)
        if direct is not None:
            return direct
    if target.on:
        target = replace(
            target,
            color_temp=state.color_temp if target.color_temp is None else target.color_temp,
            level=state.level if target.level is None else target.level,
        )

    if _satisfies(state, target):
        return []
    commands = _ordered_commands(profile)
    seen = {state}
    queue = deque([(state, [])])
    while queue:
        current, path = queue.popleft()
        if len(path) >= max_length:
            break
        for name in commands:
            following = apply(profile, current, name)
            if following in seen:
                continue
            chain = [*path, name]
            if _satisfies(following, target):
                return chain
            seen.add(following)
            queue.append((following, chain))
    raise PlanError(f"No chain of at most {max_length} commands reaches {target}")
