"""Device profiles: everything that differs between light models, as data.

A profile lives in ``profiles/<id>/profile.yaml``. The core (codec, planner,
remote tracking, beacons) reads only these objects, so a similar device with
other commands or levels needs a new profile, not new code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).parent / "profiles"
POWER_VALUES = ("on", "off", "toggle")


class ProfileError(ValueError):
    """A profile file that cannot be used. The message starts with the file path."""


@dataclass(frozen=True)
class Effect:
    """What a command does to the light's state."""

    power: str | None = None  # "on", "off" or "toggle"
    brightness_step: int = 0  # +1 = one level brighter
    color_temp_step: int = 0  # +1 = next value in color_temps, wrapping
    color_temp: int | None = None  # kelvin
    level: int | None = None  # percent, one of brightness_levels
    scene: str | None = None


@dataclass(frozen=True)
class Command:
    name: str
    template: str
    effect: Effect | None

    def payload(self, transaction: int) -> bytes:
        if not 0x80 <= transaction <= 0xFF:
            raise ValueError("Transaction byte must be in 0x80..0xFF")
        return bytes.fromhex(self.template.replace("TT", f"{transaction:02X}"))

    def matches(self, payload: bytes) -> bool:
        """Equal to this command, whatever the transaction byte."""
        return len(payload) > 3 and payload == self.payload(0x80)[:3] + payload[3:4] + self.payload(0x80)[4:]


@dataclass(frozen=True)
class RemoteRules:
    address: str | int  # "mac_low15" or a fixed address
    sequence_step: int


@dataclass(frozen=True)
class PairingRules:
    command: str
    requests: int
    interval: float
    after_power_on: bool


@dataclass(frozen=True)
class BeaconRules:
    ad_type: int
    uuid_prefix: bytes
    mac_offset: int
    flag_offset: int
    active_timeout: float
    quiet_timeout: float
    # 16-bit service UUID whose service data carries the same UUID (without the beacon-type byte).
    service_uuid: int | None = None


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    manufacturer: str
    model: str
    net_key: bytes
    app_key: bytes
    iv_index: int
    ttl: int
    destination: int
    brightness_levels: tuple[int, ...]  # brightest first
    color_temps: tuple[int, ...]  # ascending kelvin
    power_on: Effect
    commands: Mapping[str, Command]
    scenes: tuple[str, ...]
    events: tuple[str, ...]
    remote: RemoteRules
    pairing: PairingRules
    beacon: BeaconRules
    remote_layout: Mapping[str, Any] | None = None  # remote.json: how the remote card draws it
    texts: str = ""  # the set of translated wizard step variants (see _texts)


    def identify(self, payload: bytes) -> str | None:
        for command in self.commands.values():
            if command.matches(payload):
                return command.name
        return None


class _Reader:
    """Typed access to the raw YAML with errors that name the file and the key."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def fail(self, key: str, problem: str) -> ProfileError:
        return ProfileError(f"{self.path}: {key}: {problem}")

    def get(self, data: Mapping[str, Any], key: str, kind: type | tuple[type, ...], where: str = "") -> Any:
        name = f"{where}{key}"
        if not isinstance(data, Mapping) or key not in data:
            raise self.fail(name, "missing")
        value = data[key]
        if isinstance(value, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,)):
            raise self.fail(name, f"expected {kind}, got a boolean")
        if not isinstance(value, kind):
            raise self.fail(name, f"expected {kind}, got {type(value).__name__}")
        return value

    def hex_bytes(self, data: Mapping[str, Any], key: str, where: str, length: int | None = None) -> bytes:
        text = self.get(data, key, str, where)
        try:
            value = bytes.fromhex(text)
        except ValueError:
            raise self.fail(f"{where}{key}", "not hex") from None
        if length is not None and len(value) != length:
            raise self.fail(f"{where}{key}", f"expected {length} bytes")
        return value


TEXT_SET = re.compile(r"[a-z0-9_]+")


def _texts(reader: _Reader, data: Mapping[str, Any], profile_id: str) -> str:
    """The profile's set of wizard texts (reset and remote pairing hints): translated step variants
    `<step>_<texts>` in the integration's translations, so the frontend shows them in the user's
    language; a flow does not know its user, so the server could only use its own language."""
    texts = str(data.get("texts", profile_id))
    if not TEXT_SET.fullmatch(texts):
        raise reader.fail("texts", "only a-z, 0-9 and _ (it names translation keys)")
    return texts


def _template(reader: _Reader, name: str, template: str) -> str:
    where = f"commands.{name}.payload"
    parts = template.split()
    if len(parts) < 4 or parts[3] != "TT" or parts.count("TT") != 1:
        raise reader.fail(where, "the 4th byte must be TT (transaction) and TT must appear once")
    try:
        bytes.fromhex(template.replace("TT", "80"))
    except ValueError:
        raise reader.fail(where, "not hex") from None
    if len(parts) > 11:
        raise reader.fail(where, "longer than 11 bytes (unsegmented access payload)")
    return template


_EFFECT_TYPES = {
    "power": str,
    "brightness_step": int,
    "color_temp_step": int,
    "color_temp": int,
    "level": int,
    "scene": str,
}


def _effect(reader: _Reader, data: Any, where: str, levels, temps) -> Effect:
    if not isinstance(data, Mapping) or not data:
        raise reader.fail(where, "must be a non-empty mapping")
    unknown = set(data) - set(_EFFECT_TYPES)
    if unknown:
        raise reader.fail(where, f"unknown keys {sorted(unknown)}")
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, _EFFECT_TYPES[key]):
            raise reader.fail(f"{where}.{key}", f"expected {_EFFECT_TYPES[key].__name__}")
    effect = Effect(**data)
    # The shapes state.apply understands; anything else would be loaded and silently ignored.
    absolute = effect.color_temp is not None and effect.level is not None
    shapes = (
        absolute and effect.power in (None, "on")
        and not effect.brightness_step and not effect.color_temp_step,
        effect.power is not None and effect.color_temp is None and effect.level is None
        and not effect.brightness_step and not effect.color_temp_step and effect.scene is None,
        effect.color_temp_step != 0 and effect.power is None and effect.color_temp is None
        and not effect.brightness_step and effect.scene is None,
        effect.brightness_step != 0 and effect.power is None and effect.color_temp is None
        and effect.level is None and not effect.color_temp_step and effect.scene is None,
    )
    if not any(shapes):
        raise reader.fail(
            where,
            "unsupported combination: use a scene (color_temp + level), power alone, "
            "color_temp_step (+ level) or brightness_step alone",
        )
    if effect.power is not None and effect.power not in POWER_VALUES:
        raise reader.fail(f"{where}.power", f"must be one of {POWER_VALUES}")
    if effect.level is not None and effect.level not in levels:
        raise reader.fail(f"{where}.level", f"{effect.level} is not one of brightness_levels {list(levels)}")
    if effect.color_temp is not None and effect.color_temp not in temps:
        raise reader.fail(
            f"{where}.color_temp", f"{effect.color_temp} is not one of color_temps {list(temps)}"
        )
    return effect


def load_profile(path: Path) -> Profile:
    reader = _Reader(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        raise ProfileError(f"{path}: cannot read: {err}") from err
    if not isinstance(data, Mapping):
        raise ProfileError(f"{path}: not a YAML mapping")

    network = reader.get(data, "network", Mapping)
    levels = tuple(reader.get(data, "brightness_levels", list))
    temps = tuple(reader.get(data, "color_temps", list))
    for key, values in (("brightness_levels", levels), ("color_temps", temps)):
        if not values or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values):
            raise reader.fail(key, "must be a non-empty list of positive integers")
    if any(a <= b for a, b in zip(levels, levels[1:], strict=False)):
        raise reader.fail("brightness_levels", "must list distinct levels, brightest first")
    if any(a >= b for a, b in zip(temps, temps[1:], strict=False)):
        raise reader.fail("color_temps", "must list distinct kelvin values in ascending order")

    commands: dict[str, Command] = {}
    for name, spec in reader.get(data, "commands", Mapping).items():
        where = f"commands.{name}"
        template = _template(reader, name, reader.get(spec, "payload", str, f"{where}."))
        effect_data = spec.get("effect") if isinstance(spec, Mapping) else None
        effect = (
            None if effect_data is None else _effect(reader, effect_data, f"{where}.effect", levels, temps)
        )
        commands[str(name)] = Command(str(name), template, effect)

    def names(key: str) -> tuple[str, ...]:
        values = tuple(str(v) for v in reader.get(data, key, list))
        missing = [v for v in values if v not in commands]
        if missing:
            raise reader.fail(key, f"not commands: {missing}")
        return values

    scenes = names("scenes")
    # The scene is told from the lamp's colour temperature and level (state.scene_of): every scene
    # must set both, and no two scenes may set the same pair.
    seen: dict[tuple[int, int], str] = {}
    for scene in scenes:
        effect = commands[scene].effect
        if effect is None or effect.color_temp is None or effect.level is None:
            raise reader.fail("scenes", f"{scene!r} does not set colour temperature and level")
        pair = (effect.color_temp, effect.level)
        if pair in seen:
            raise reader.fail("scenes", f"{scene!r} and {seen[pair]!r} set the same temperature and level")
        seen[pair] = scene
    events = names("events")

    remote = reader.get(data, "remote", Mapping)
    address = reader.get(remote, "address", (str, int), "remote.")
    if isinstance(address, str) and address != "mac_low15":
        raise reader.fail("remote.address", 'must be "mac_low15" or a fixed address')
    if isinstance(address, int) and not 0x0001 <= address <= 0x7FFF:
        raise reader.fail("remote.address", "a fixed address must be unicast (0x0001-0x7FFF)")

    pairing = reader.get(data, "pairing", Mapping)
    pairing_rules = PairingRules(
        command=reader.get(pairing, "command", str, "pairing."),
        requests=reader.get(pairing, "requests", int, "pairing."),
        interval=float(reader.get(pairing, "interval", (int, float), "pairing.")),
        after_power_on=reader.get(pairing, "after_power_on", bool, "pairing."),
    )
    for old in ("reset_hint", "remote_hint"):
        if old in pairing:
            raise reader.fail(f"pairing.{old}", "replaced by texts: the hints live in the translations")
    if pairing_rules.requests <= 0 or pairing_rules.interval <= 0:
        raise reader.fail("pairing", "requests and interval must be positive")
    if pairing_rules.command not in commands:
        raise reader.fail("pairing.command", f"{pairing_rules.command!r} is not a command")

    beacon = reader.get(data, "beacon", Mapping)
    power_on = _effect(reader, reader.get(data, "power_on", Mapping), "power_on", levels, temps)
    sequence_step = reader.get(remote, "sequence_step", int, "remote.")
    if sequence_step <= 0:
        raise reader.fail("remote.sequence_step", "must be positive")
    for key in ("active_timeout", "quiet_timeout"):
        if reader.get(beacon, key, (int, float), "beacon.") <= 0:
            raise reader.fail(f"beacon.{key}", "must be positive")

    return Profile(
        id=reader.get(data, "id", str),
        name=reader.get(data, "name", str),
        manufacturer=reader.get(data, "manufacturer", str),
        model=reader.get(data, "model", str),
        net_key=reader.hex_bytes(network, "net_key", "network.", 16),
        app_key=reader.hex_bytes(network, "app_key", "network.", 16),
        iv_index=reader.get(network, "iv_index", int, "network."),
        ttl=reader.get(network, "ttl", int, "network."),
        destination=reader.get(network, "destination", int, "network."),
        brightness_levels=levels,
        color_temps=temps,
        power_on=power_on,
        commands=commands,
        scenes=scenes,
        events=events,
        remote=RemoteRules(address=address, sequence_step=sequence_step),
        pairing=pairing_rules,
        beacon=BeaconRules(
            ad_type=reader.get(beacon, "ad_type", int, "beacon."),
            uuid_prefix=reader.hex_bytes(beacon, "uuid_prefix", "beacon."),
            mac_offset=reader.get(beacon, "mac_offset", int, "beacon."),
            flag_offset=reader.get(beacon, "flag_offset", int, "beacon."),
            active_timeout=float(reader.get(beacon, "active_timeout", (int, float), "beacon.")),
            quiet_timeout=float(reader.get(beacon, "quiet_timeout", (int, float), "beacon.")),
            service_uuid=(
                reader.get(beacon, "service_uuid", int, "beacon.") if "service_uuid" in beacon else None
            ),
        ),
        remote_layout=_remote_layout(path.parent / "remote.json", commands, pairing_rules.command),
        texts=_texts(reader, data, reader.get(data, "id", str)),
    )


LAYOUTS = ("vertical", "horizontal", "mini")
GROUP_KINDS = ("row", "ring", "grid")
BUTTON_KEYS = {"label", "short", "icon"}


def _remote_layout(path: Path, commands: Mapping[str, Command], pairing: str) -> Mapping[str, Any] | None:
    """The card's layout of the remote (remote.json next to profile.yaml), checked; None if absent."""
    if not path.exists():
        return None
    reader = _Reader(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise reader.fail("remote.json", f"not JSON: {err}") from None

    def command(value: Any, where: str) -> None:
        if value not in commands or value == pairing:
            raise reader.fail(where, f"{value!r} is not a command of the profile")

    for name, button in reader.get(data, "buttons", Mapping).items():
        command(name, f"buttons.{name}")
        if not isinstance(button, Mapping) or set(button) - BUTTON_KEYS:
            raise reader.fail(f"buttons.{name}", f"only {sorted(BUTTON_KEYS)}")
        icon = button.get("icon")
        if icon is not None and not (isinstance(icon, str) and icon.startswith(("valen:", "mdi:"))):
            raise reader.fail(f"buttons.{name}.icon", "must start with valen: or mdi:")
    groups = reader.get(data, "groups", Mapping)
    for name, group in groups.items():
        where = f"groups.{name}"
        kind = reader.get(group, "kind", str, f"{where}.")
        if kind not in GROUP_KINDS:
            raise reader.fail(f"{where}.kind", f"one of {list(GROUP_KINDS)}")
        if kind == "row":
            for value in reader.get(group, "buttons", list, f"{where}."):
                command(value, f"{where}.buttons")
            if not isinstance(group.get("wrap", False), bool):
                raise reader.fail(f"{where}.wrap", "true or false")
        elif kind == "ring":
            for part in ("up", "down", "center"):
                command(reader.get(group, part, str, f"{where}."), f"{where}.{part}")
        else:
            columns = reader.get(group, "columns", int, f"{where}.")
            taken: set[tuple[int, int]] = set()
            for cell in reader.get(group, "cells", list, f"{where}."):
                if not (isinstance(cell, list) and len(cell) == 3):
                    raise reader.fail(f"{where}.cells", "each cell is [command, column, row]")
                value, column, row = cell
                command(value, f"{where}.cells")
                inside = isinstance(column, int) and isinstance(row, int) and 1 <= column <= columns
                if not (inside and row >= 1):
                    raise reader.fail(f"{where}.cells", f"{cell} is outside {columns} columns")
                if (column, row) in taken:
                    raise reader.fail(f"{where}.cells", f"two cells at column {column}, row {row}")
                taken.add((column, row))
    layouts = reader.get(data, "layouts", Mapping)
    for name in LAYOUTS:
        columns = reader.get(layouts, name, list, "layouts.")
        used = [group for column in columns for group in column]
        missing = [group for group in used if group not in groups]
        if missing:
            raise reader.fail(f"layouts.{name}", f"no such groups: {missing}")
        if len(used) != len(set(used)):
            raise reader.fail(f"layouts.{name}", "a group is used twice")
    return data


def load_profiles(root: Path) -> tuple[dict[str, Profile], list[str]]:
    """Every profile under root by id, and an error message per profile that failed."""
    profiles: dict[str, Profile] = {}
    errors: list[str] = []
    for path in sorted(root.glob("*/profile.yaml")):
        try:
            loaded = load_profile(path)
        except ProfileError as err:
            errors.append(str(err))
            continue
        except Exception as err:  # noqa: BLE001 - one bad profile must not stop the others
            errors.append(f"{path}: unexpected error: {err!r}")
            continue
        if loaded.id in profiles:
            errors.append(f"{path}: id: {loaded.id!r} is used by another profile")
            continue
        profiles[loaded.id] = loaded
    return profiles, errors
