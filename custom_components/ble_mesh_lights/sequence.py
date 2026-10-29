"""Durable Mesh sequence counters, one per sender address.

A light drops every message whose sequence is not above the last one it accepted
from that address, so a counter that goes back makes the light ignore HA. Numbers
are reserved on disk in blocks before any of them is used, and a store that cannot
be read correctly is refused rather than restarted from zero.
Based on jphelan82/ha-philips-skylight (MIT).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

VERSION = 1
BLOCK = 256
LIMIT = 0x1000000  # sequence numbers are 24 bits
SKIP = 0x100000


class SequenceError(ValueError):
    """The counters cannot be used safely."""


class Store(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...

    async def async_save(self, data: dict[str, Any]) -> None: ...


def _key(address: int) -> str:
    return f"0x{address:04x}"


class SequenceStore:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._next: dict[int, int] = {}
        self._limit: dict[int, int] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self, *, expect_existing: bool = False) -> None:
        """Read the counters. With expect_existing (some sender is already paired), a missing
        store is refused: HA's Store returns None for a corrupt file it has set aside, and
        starting again from zero would make every paired light ignore HA."""
        data = await self._store.async_load()
        limits: dict[int, int] = {}
        if data is None and expect_existing:
            raise SequenceError("Sequence store missing but senders are paired; not starting from zero")
        if data is not None:
            if not isinstance(data, dict) or data.get("version") != VERSION:
                raise SequenceError("Unknown sequence store format; refusing to reuse sequence numbers")
            addresses = data.get("addresses")
            if not isinstance(addresses, dict):
                raise SequenceError("Sequence store has no address table; refusing to start from zero")
            for key, value in addresses.items():
                try:
                    address = int(key, 16)
                except (TypeError, ValueError):
                    raise SequenceError(f"Bad address {key!r} in the sequence store") from None
                if type(value) is not int or not 0 <= value <= LIMIT:
                    raise SequenceError(f"Bad sequence value for {key}: {value!r}")
                # The same address spelled twice ("0x7E01", "0x7e01"): never take the lower one.
                limits[address] = max(value, limits.get(address, 0))
        self._limit = dict(limits)
        self._next = dict(limits)
        self._loaded = True

    def high_watermarks(self) -> dict[int, int]:
        return dict(self._limit)

    async def _save(self, limits: dict[int, int]) -> None:
        await self._store.async_save(
            {"version": VERSION, "addresses": {_key(a): v for a, v in sorted(limits.items())}}
        )

    async def async_next(self, address: int) -> int:
        async with self._lock:
            if not self._loaded:
                raise SequenceError("Sequence store not loaded")
            value = self._next.get(address, 0)
            limit = self._limit.get(address, 0)
            if value >= LIMIT:
                raise SequenceError(f"Sequence space of {_key(address)} is exhausted; use another address")
            if value >= limit:
                new_limit = min(value + BLOCK, LIMIT)
                # Nothing from the new block is handed out until it is on disk.
                await self._save({**self._limit, address: new_limit})
                self._limit[address] = new_limit
            self._next[address] = value + 1
            return value

    async def async_skip(self, address: int, amount: int = SKIP) -> int:
        """Jump past numbers an older backup or a lost store may already have used."""
        async with self._lock:
            if not self._loaded:
                raise SequenceError("Sequence store not loaded")
            start = self._limit.get(address, 0) + amount
            if start >= LIMIT:
                raise SequenceError("Too little sequence space left to skip forward")
            await self._save({**self._limit, address: start})
            self._limit[address] = start
            self._next[address] = start
            return start
