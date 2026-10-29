"""Diagnostics download. MAC addresses stay in: they are the user's own lamps and remotes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import BleMeshLightsConfigEntry, transport
from . import hub as hub_module
from .const import PRESENCE_INTERVAL


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BleMeshLightsConfigEntry
) -> dict[str, Any]:
    hub = entry.runtime_data
    now = hub_module.monotonic()
    return {
        "presence": {"interval": PRESENCE_INTERVAL, "power_cycle_gap": hub.power_cycle_gap},
        "options": dict(entry.options),
        "data": dict(entry.data),
        "profile_errors": hub.profile_errors,
        "boards": [
            {"name": board.name, "node": board.node, "ready": transport.board_ready(hass, board)}
            for board in hub.boards
        ],
        "lamps": [
            {
                "title": entry.subentries[sub_id].title,
                "profile": lamp.profile.id,
                "mac": lamp.mac,
                "address": f"0x{lamp.address:04X}",
                "after_outage": lamp.after_outage,
                "state": asdict(lamp.state),
                "uncertain": lamp.uncertain,
                "present": lamp.present,
                "before_loss": asdict(lamp.before_loss) if lamp.before_loss else None,
                "rssi": lamp.rssi,
                "board": lamp.board,
                "last_seen": lamp.last_seen.isoformat() if lamp.last_seen else None,
                "mode": {None: "unknown", True: "quiet", False: "active"}[lamp.quiet],
                "last_advert_age": round(now - advert[1], 1)
                if (advert := transport.last_advert(hass, lamp.mac))
                else None,
            }
            for sub_id, lamp in hub.lamps.items()
        ],
        "counters": {f"0x{address:04x}": value for address, value in hub.counters.high_watermarks().items()},
        "remotes": {
            key: {"source": f"0x{r.source:04X}", "lamps": r.lamps, "missed_total": r.missed_total}
            for key, r in hub.remotes.items()
        },
        "unknown_remotes": sorted(hub.unknown_remotes),
        "ignored_remotes": sorted(hub.ignored_remotes),
        "heard_sources": [f"0x{source:04X}" for source in sorted(hub.heard_sources)],
    }
