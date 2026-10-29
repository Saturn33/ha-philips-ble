"""Bluetooth Mesh codec: the worked example from docs/PROTOCOL.md and the captured remote presses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

mesh = load("mesh")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "valen_air.jsonl"
NET_KEY = bytes.fromhex("11" * 16)
APP_KEY = bytes(16)
WORKED_EXAMPLE = bytes.fromhex("192a1b2e29b7b497858ed13dbe72070dec1674d7b83da5b23ed5")
REMOTE_MACS = {"E8:26:CF:6B:37:AF", "18:90:67:6C:13:B3", "18:90:67:09:23:CF", "E8:26:CF:C9:89:FC"}


@pytest.fixture
def codec():
    return mesh.MeshCodec(NET_KEY, APP_KEY)


def test_worked_example(codec):
    assert codec.decode(WORKED_EXAMPLE) == [
        mesh.MeshMessage(
            source=0x37AF, destination=0xFFFF, sequence=2280, ttl=5, payload=bytes.fromhex("c81102810300")
        )
    ]


def test_derived_values(codec):
    assert codec.nid == 0x1B
    assert codec.aid == 0x38
    assert codec.network_id.hex() == "c7cd5342d1544cd5"


def test_roundtrip(codec):
    payload = bytes.fromhex("c8110285030141")
    assert codec.decode(codec.encode(payload, 12345, 0x7E01)) == [
        mesh.MeshMessage(0x7E01, 0xFFFF, 12345, 5, payload)
    ]


def test_other_keys_ignored():
    assert mesh.MeshCodec(bytes.fromhex("22" * 16), APP_KEY).decode(WORKED_EXAMPLE) == []


@pytest.mark.parametrize(
    ("payload", "sequence"), [(b"", 0), (bytes(12), 0), (b"\x01", 0x1000000), (b"\x01", -1)]
)
def test_encode_limits(codec, payload, sequence):
    with pytest.raises(ValueError):
        codec.encode(payload, sequence, 1)


def test_fixture_remote_messages(codec):
    checked = 0
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["mac"] not in REMOTE_MACS:
            continue
        raw = bytes.fromhex(record["raw"])
        if not any(ad_type == 0x2A for ad_type, _ in mesh.ad_structures(raw)):
            continue
        messages = codec.decode(raw)
        assert messages, record
        expected = int(record["mac"][-5:].replace(":", ""), 16) & 0x7FFF
        assert all(m.source == expected for m in messages)
        checked += 1
    assert checked == 483
