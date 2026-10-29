"""Sender addresses for HA: unicast, never a remote's address, never reused."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

addressing = load("addressing")


def test_allocate():
    assert addressing.allocate(0x7E00, [], []) == (1, 0x7E01)
    assert addressing.allocate(0x7E00, [1, 2], []) == (3, 0x7E03)
    assert addressing.allocate(0x7E00, [1], [0x7E02]) == (3, 0x7E03)


def test_allocate_stays_unicast():
    with pytest.raises(ValueError):
        addressing.allocate(0x7FF0, range(1, 15), [])
    with pytest.raises(ValueError):
        addressing.allocate(0x8000, [], [])


def test_check_sender():
    with pytest.raises(ValueError):
        addressing.check_sender(0x7FFF, [])
    with pytest.raises(ValueError):
        addressing.check_sender(0x37AF, [0x37AF])
    with pytest.raises(ValueError):
        addressing.check_sender(0x8001, [])
    with pytest.raises(ValueError):
        addressing.check_sender(0x0000, [])
    addressing.check_sender(0x7E01, [0x37AF])


def test_integration_range():
    # Research tools must not send as HA's own lamp addresses: two counters for one address
    # would make the light drop whichever is behind.
    assert addressing.in_integration_range(0x7E01)
    assert addressing.in_integration_range(0x7EFF)
    assert not addressing.in_integration_range(0x7FFE)
    assert addressing.in_integration_range(0x1101, base=0x1100)


def test_tool_examples_avoid_integration_addresses():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "tools" / "send.py", root / "tools" / "README.md"):
        assert "--src 0x7E" not in path.read_text(encoding="utf-8"), path
