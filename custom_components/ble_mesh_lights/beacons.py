"""Lamp beacons: whether a lamp has mains power.

A powered lamp keeps sending an unprovisioned-device beacon with its own address
in it. For 30 minutes after power-on it sends continuously (active); after that,
once a minute (quiet), with a flag byte telling which. The beacon says nothing
about on/off or brightness: only whether the wall switch has power.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mesh import ad_structures
from .profile import Profile

RESTORED = "restored"


@dataclass(frozen=True)
class Beacon:
    mac: str
    quiet: bool


AD_SERVICE_DATA16 = 0x16


def parse_beacon(profile: Profile, advertisement: bytes) -> Beacon | None:
    """The lamp's beacon (AD 0x2B), or the same UUID carried as service data in its connectable
    advert (active mode), which is what HA keeps as the last raw advert while the lamp is active."""
    rules = profile.beacon
    for ad_type, value in ad_structures(advertisement):
        if (
            rules.service_uuid is not None
            and ad_type == AD_SERVICE_DATA16
            and value[:2] == rules.service_uuid.to_bytes(2, "little")
        ):
            # Same layout as the beacon without its beacon-type byte: put that byte back.
            value = rules.uuid_prefix[:1] + value[2:]
        elif ad_type != rules.ad_type:
            continue
        if not value.startswith(rules.uuid_prefix):
            continue
        if len(value) <= max(rules.mac_offset + 5, rules.flag_offset):
            continue
        address = value[rules.mac_offset : rules.mac_offset + 6][::-1]
        return Beacon(":".join(f"{b:02X}" for b in address), quiet=value[rules.flag_offset] == 0x01)
    return None


def lost_timeout(profile: Profile, beacon: Beacon) -> float:
    """How long without a beacon before the lamp counts as switched off at the wall."""
    return profile.beacon.quiet_timeout if beacon.quiet else profile.beacon.active_timeout


@dataclass
class _Lamp:
    last_seen: float
    last_beacon: Beacon | None  # None: watched but not seen yet
    present: bool | None


class PresenceTracker:
    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._lamps: dict[str, _Lamp] = {}

    def watch(self, mac: str, now: float, lost: bool = False) -> None:
        """Expect this lamp (a configured one): if no beacon comes within the quiet timeout, it
        counts as lost, so a lamp without power at HA start does not stay unknown forever.
        `lost`: it had no power when the entry stopped; its next active beacon is a restore."""
        if mac not in self._lamps:
            self._lamps[mac] = _Lamp(now, None, False if lost else None)

    def seen(self, beacon: Beacon, now: float) -> str | None:
        """Record a beacon; RESTORED when a lamp marked lost comes back with an active beacon.

        Only an active beacon proves that mains power just returned. A quiet one after a
        loss means a missed burst, and the first sighting of a lamp is not a restore: HA
        cannot tell how long it has been powered.
        """
        lamp = self._lamps.get(beacon.mac)
        result = None
        if lamp is not None and lamp.present is False and not beacon.quiet:
            result = RESTORED
        self._lamps[beacon.mac] = _Lamp(now, beacon, True)
        return result

    def expired(self, now: float) -> list[str]:
        """Lamps that just went quiet for longer than their timeout; each once per loss."""
        lost = []
        for mac, lamp in self._lamps.items():
            timeout = (
                self._profile.beacon.quiet_timeout
                if lamp.last_beacon is None
                else lost_timeout(self._profile, lamp.last_beacon)
            )
            if lamp.present is not False and now - lamp.last_seen > timeout:
                lamp.present = False
                lost.append(mac)
        return lost

    def present(self, mac: str) -> bool | None:
        lamp = self._lamps.get(mac)
        return None if lamp is None else lamp.present
