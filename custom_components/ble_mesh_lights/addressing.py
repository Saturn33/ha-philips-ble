"""HA's own sender addresses: unicast, never a remote's, never reused.

A sender address must be unicast (0x0001-0x7FFF); 0x8000 and above are virtual
and group addresses. Sending as a remote's address would make its light drop
the real remote's presses (sequence replay protection).
"""

from __future__ import annotations

from collections.abc import Collection

UNICAST_MIN = 0x0001
UNICAST_MAX = 0x7FFF
# 0x7FFF is the Philips Skylight remote's fixed address.
FORBIDDEN = frozenset({0x7FFF})
DEFAULT_BASE = 0x7E00
INTEGRATION_SPAN = 0x100  # addresses base + 1 .. base + 255 belong to the integration's lamps


def in_integration_range(address: int, base: int = DEFAULT_BASE) -> bool:
    """Whether the integration may give this address to a lamp. Other senders (research tools)
    must stay out: one address with two sequence counters makes the light drop the lower one."""
    return base < address < base + INTEGRATION_SPAN


def check_sender(address: int, remotes: Collection[int]) -> None:
    if not UNICAST_MIN <= address <= UNICAST_MAX:
        raise ValueError(f"0x{address:04X} is not a unicast address (0x0001-0x7FFF)")
    if address in FORBIDDEN or address in remotes:
        raise ValueError(f"0x{address:04X} belongs to a remote; sending as it would break that remote")


def allocate(base: int, used_indices: Collection[int], avoid: Collection[int]) -> tuple[int, int]:
    """The next free (index, address): address = base + index, index from 1, never reused."""
    index = max(used_indices, default=0) + 1
    while True:
        address = base + index
        if not UNICAST_MIN <= address <= UNICAST_MAX or address in FORBIDDEN:
            raise ValueError(f"No unicast address left above base 0x{base:04X}")
        if address not in avoid:
            return index, address
        index += 1
