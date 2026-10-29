"""Physical remotes: which press was this, was it a repeat, were presses missed?

A remote sends each press as one short burst, so some presses are never heard.
Its sequence number advances by ``sequence_step`` per press (and by one per
repeat while a button is held), which shows how many presses were missed.
Several boards may hear the same message, sometimes out of order.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from .mesh import MeshMessage
from .profile import Profile

LATE_WINDOW = 3  # presses: an older press from another board, not a new session
PAIRING_SESSION = 5.0  # seconds between pairing requests of one session


def remote_address(profile: Profile, mac: str) -> int:
    rule = profile.remote.address
    if isinstance(rule, int):
        return rule
    return int(mac[-5:].replace(":", ""), 16) & 0x7FFF


@dataclass(frozen=True)
class RemotePress:
    source: int
    sequence: int
    command: str
    # Messages not heard that may have changed the lamp: whole presses since the previous one,
    # plus the start or repeats of a held button. Gaps in commands without an effect (pairing)
    # do not count.
    missed: int
    repeat: bool  # the same press continues (held button, pairing session)
    late: bool  # an older press heard after a newer one: report, do not apply


@dataclass
class _Remote:
    press: int
    command: str
    sequence: int
    last_pairing: float | None = None


class RemoteTracker:
    def __init__(self, profile: Profile, ignore_sources: Iterable[int] = (), history: int = 512) -> None:
        self._profile = profile
        self._ignored = frozenset(ignore_sources)
        self._history = history
        self._seen: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._remotes: dict[int, _Remote] = {}

    def set_ignored(self, sources: Iterable[int]) -> None:
        self._ignored = frozenset(sources)

    def snapshot(self) -> dict[str, dict]:
        """Per-remote baselines to store between HA restarts (JSON-compatible)."""
        return {
            f"0x{source:04x}": {"press": r.press, "command": r.command, "sequence": r.sequence}
            for source, r in self._remotes.items()
        }

    def restore(self, data: dict[str, dict]) -> None:
        for key, value in data.items():
            self._remotes[int(key, 16)] = _Remote(value["press"], value["command"], value["sequence"])

    def _has_effect(self, command: str) -> bool:
        return self._profile.commands[command].effect is not None

    def accept(self, message: MeshMessage, now: float) -> RemotePress | None:
        if message.destination != self._profile.destination or message.source in self._ignored:
            return None
        command = self._profile.identify(message.payload)
        if command is None:
            return None
        key = (message.source, message.sequence)
        if key in self._seen:
            return None
        self._seen[key] = None
        if len(self._seen) > self._history:
            self._seen.popitem(last=False)

        press = message.sequence // self._profile.remote.sequence_step
        remote = self._remotes.get(message.source)
        pairing = command == self._profile.pairing.command
        if remote is None:
            first = _Remote(press, command, message.sequence, now if pairing else None)
            self._remotes[message.source] = first
            return RemotePress(message.source, message.sequence, command, 0, False, False)

        in_pairing_session = (
            pairing and remote.last_pairing is not None and now - remote.last_pairing < PAIRING_SESSION
        )
        if in_pairing_session and press < remote.press:
            # A request of the running session heard late through another board.
            remote.last_pairing = now
            return RemotePress(message.source, message.sequence, command, 0, True, False)
        if remote.press - LATE_WINDOW <= press < remote.press:
            return RemotePress(message.source, message.sequence, command, 0, False, True)

        step = self._profile.remote.sequence_step
        effect = self._has_effect(command)
        if in_pairing_session:
            missed, repeat = 0, True
        elif press > remote.press:
            # Messages of this press before the one heard (a held button whose start was missed).
            start = message.sequence - press * step if effect else 0
            missed, repeat = press - remote.press - 1 + start, False
        elif press == remote.press and command == remote.command:
            gap = message.sequence - remote.sequence - 1
            missed, repeat = (max(gap, 0) if effect else 0), True
        elif press == remote.press:
            missed, repeat = 0, False
        else:  # far below: the remote's counter went back (battery change, wrap)
            missed, repeat = 0, False

        remote.press = press
        remote.command = command
        remote.sequence = message.sequence
        remote.last_pairing = now if pairing else None
        return RemotePress(message.source, message.sequence, command, missed, repeat, False)
