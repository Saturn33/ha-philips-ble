"""Durable per-address sequence counters: reserve on disk before use, never go back."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

sequence = load("sequence")


class FakeStore:
    """In-memory stand-in for HA's Store; keeps every saved snapshot."""

    def __init__(self, data=None, fail_saves: int = 0):
        self.data = data
        self.saves: list[dict] = []
        self.fail_saves = fail_saves

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        if self.fail_saves:
            self.fail_saves -= 1
            raise OSError("disk full")
        self.data = copy.deepcopy(data)
        self.saves.append(self.data)


async def _loaded(store: FakeStore):
    counters = sequence.SequenceStore(store)
    await counters.async_load()
    return counters


async def test_reserves_before_use():
    store = FakeStore()
    counters = await _loaded(store)
    assert await counters.async_next(0x7E01) == 0
    assert store.data == {"version": 1, "addresses": {"0x7e01": 256}}
    for expected in range(1, 256):
        assert await counters.async_next(0x7E01) == expected
    assert len(store.saves) == 1
    assert await counters.async_next(0x7E01) == 256
    assert store.data["addresses"]["0x7e01"] == 512


async def test_addresses_independent():
    counters = await _loaded(FakeStore())
    assert await counters.async_next(0x7E01) == 0
    assert await counters.async_next(0x7E02) == 0
    assert await counters.async_next(0x7E01) == 1
    assert counters.high_watermarks() == {0x7E01: 256, 0x7E02: 256}


async def test_reload_continues_above_reservation():
    store = FakeStore()
    counters = await _loaded(store)
    for _ in range(3):
        await counters.async_next(0x7E01)
    again = await _loaded(store)
    assert await again.async_next(0x7E01) == 256


async def test_failed_save_returns_nothing():
    store = FakeStore(fail_saves=1)
    counters = await _loaded(store)
    with pytest.raises(OSError):
        await counters.async_next(0x7E01)
    first = await counters.async_next(0x7E01)
    second = await counters.async_next(0x7E01)
    assert second > first >= 0
    assert store.data["addresses"]["0x7e01"] > second


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"version": 1},
        {"version": 2, "addresses": {}},
        {"version": 1, "addresses": {"0x7e01": -1}},
        {"version": 1, "addresses": "x"},
        {"version": 1, "addresses": {"zz": 5}},
        {"version": 1, "addresses": {"0x7e01": 0x1000001}},
        {"version": 1, "addresses": {"0x7e01": "12"}},
        ["not", "a", "mapping"],
    ],
)
async def test_corrupt_store_refuses(data):
    counters = sequence.SequenceStore(FakeStore(data))
    with pytest.raises(sequence.SequenceError):
        await counters.async_load()


async def test_fresh_and_new_address():
    counters = await _loaded(FakeStore(None))
    assert await counters.async_next(0x7E01) == 0
    counters = await _loaded(FakeStore({"version": 1, "addresses": {"0x7e01": 512}}))
    assert await counters.async_next(0x7E02) == 0
    assert await counters.async_next(0x7E01) == 512


async def test_next_before_load_refuses():
    counters = sequence.SequenceStore(FakeStore())
    with pytest.raises(sequence.SequenceError):
        await counters.async_next(0x7E01)


async def test_skip_and_exhaustion():
    store = FakeStore({"version": 1, "addresses": {"0x7e01": 512}})
    counters = await _loaded(store)
    assert await counters.async_skip(0x7E01) == 512 + 0x100000
    assert store.data["addresses"]["0x7e01"] == 512 + 0x100000
    assert await counters.async_next(0x7E01) == 512 + 0x100000

    counters = await _loaded(FakeStore({"version": 1, "addresses": {"0x7e01": 0xFFFFFF}}))
    assert await counters.async_next(0x7E01) == 0xFFFFFF
    with pytest.raises(sequence.SequenceError):
        await counters.async_next(0x7E01)


async def test_missing_store_refused_when_counters_must_exist():
    # HA's Store renames a corrupt file and returns None: once lamps are paired, that is not a fresh start.
    counters = sequence.SequenceStore(FakeStore(None))
    with pytest.raises(sequence.SequenceError):
        await counters.async_load(expect_existing=True)
    counters = sequence.SequenceStore(FakeStore({"version": 1, "addresses": {"0x7e01": 256}}))
    await counters.async_load(expect_existing=True)
    assert await counters.async_next(0x7E01) == 256


async def test_same_address_in_two_spellings_keeps_the_highest():
    data = {"version": 1, "addresses": {"0x7E01": 900, "0x7e01": 100}}
    counters = await _loaded(FakeStore(data))
    assert await counters.async_next(0x7E01) == 900
