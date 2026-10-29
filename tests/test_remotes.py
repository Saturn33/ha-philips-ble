"""Remote press tracking: deduplication, missed presses, holds, late arrivals."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

profile = load("profile")
mesh = load("mesh")
remotes = load("remotes")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "valen_air.jsonl"
R1 = "E8:26:CF:6B:37:AF"
R1_SOURCE = 0x37AF


@pytest.fixture(scope="module")
def valen():
    return profile.load_profile(profile.PROFILES_DIR / "valen" / "profile.yaml")


def message(valen, seq: int, command: str, source: int = R1_SOURCE):
    payload = valen.commands[command].payload(0x80 | (seq & 0x7F))
    return mesh.MeshMessage(source, 0xFFFF, seq, 5, payload)


def feed(tracker, valen, items, start: float = 0.0):
    """items: (sequence, command) pairs, one second apart."""
    return [tracker.accept(message(valen, seq, cmd), start + i) for i, (seq, cmd) in enumerate(items)]


def test_address_from_mac(valen):
    assert remotes.remote_address(valen, R1) == 0x37AF
    assert remotes.remote_address(valen, "E8:26:CF:C9:89:FC") == 0x09FC


def test_presses_in_order(valen):
    presses = feed(remotes.RemoteTracker(valen), valen, [(2280, "off"), (2290, "on"), (2300, "off")])
    assert [(p.sequence, p.missed, p.repeat, p.late) for p in presses] == [
        (2280, 0, False, False),
        (2290, 0, False, False),
        (2300, 0, False, False),
    ]


def test_missed_presses(valen):
    presses = feed(remotes.RemoteTracker(valen), valen, [(2850, "off"), (2880, "on")])
    assert presses[1].missed == 2


def test_hold_repeats(valen):
    items = [(3460, "brightness_down"), (3461, "brightness_down"), (3462, "brightness_down"),
             (3470, "color_temperature")]
    presses = feed(remotes.RemoteTracker(valen), valen, items)
    assert [(p.repeat, p.missed) for p in presses] == [(False, 0), (True, 0), (True, 0), (False, 0)]


def test_command_change_within_press_number(valen):
    items = [(seq, "pairing") for seq in range(2961, 2966)] + [(2967, "off")]
    presses = feed(remotes.RemoteTracker(valen), valen, items)
    assert presses[-1].command == "off"
    assert presses[-1].repeat is False


def test_duplicate_from_second_board_ignored(valen):
    tracker = remotes.RemoteTracker(valen)
    first = tracker.accept(message(valen, 2300, "off"), 0)
    assert first is not None
    assert tracker.accept(message(valen, 2300, "off"), 0.1) is None
    assert tracker.accept(message(valen, 2310, "on"), 1) is not None
    assert tracker.accept(message(valen, 2300, "off"), 1.1) is None


def test_sequence_going_back_rebaselines(valen):
    presses = feed(remotes.RemoteTracker(valen), valen, [(5000, "off"), (120, "on"), (130, "off")])
    assert (presses[1].missed, presses[1].late, presses[1].repeat) == (0, False, False)
    assert presses[2].missed == 0


def test_late_arrival(valen):
    presses = feed(remotes.RemoteTracker(valen), valen,
                   [(2300, "off"), (2310, "on"), (2290, "off"), (2320, "off")])
    assert presses[2].late is True
    assert presses[3].missed == 0
    assert presses[3].late is False


def test_own_and_foreign_ignored(valen):
    tracker = remotes.RemoteTracker(valen, ignore_sources=[0x7E01])
    assert tracker.accept(message(valen, 10, "off", source=0x7E01), 0) is None
    foreign = mesh.MeshMessage(R1_SOURCE, 0xFFFF, 10, 5, bytes.fromhex("c8110280ff"))
    assert tracker.accept(foreign, 0) is None
    other_destination = mesh.MeshMessage(R1_SOURCE, 0x0001, 20, 5, valen.commands["off"].payload(0x80))
    assert tracker.accept(other_destination, 0) is None
    tracker.set_ignored([R1_SOURCE])
    assert tracker.accept(message(valen, 30, "off"), 0) is None


def _fixture_presses(valen, file: str, mac: str = R1):
    codec = mesh.MeshCodec(valen.net_key, valen.app_key, valen.iv_index)
    tracker = remotes.RemoteTracker(valen)
    presses = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["file"] != file or record["mac"] != mac:
            continue
        for msg in codec.decode(bytes.fromhex(record["raw"])):
            press = tracker.accept(msg, record["t"])
            if press is not None:
                presses.append(press)
    return presses


def test_pairing_session_is_one_press(valen):
    pairing = [p for p in _fixture_presses(valen, "05-pair.jsonl") if p.command == "pairing"]
    assert len(pairing) == 105
    assert sum(1 for p in pairing if not p.repeat) == 1


def test_fixture_sessions(valen):
    # A new tracker per capture file: one tracker across files would count the gaps between sessions.
    missed = defaultdict(int)
    for file in ("09-pause-rx2.jsonl", "03-buttons-R1.jsonl"):
        for press in _fixture_presses(valen, file):
            missed[file] += press.missed
    assert missed == {"09-pause-rx2.jsonl": 2, "03-buttons-R1.jsonl": 2}


def test_missed_hold_repeat_is_reported(valen):
    presses = feed(remotes.RemoteTracker(valen), valen,
                   [(3450, "off"), (3460, "brightness_down"), (3462, "brightness_down")])
    assert (presses[2].repeat, presses[2].missed) == (True, 1)


def test_missed_hold_start_is_reported(valen):
    presses = feed(remotes.RemoteTracker(valen), valen, [(3450, "off"), (3461, "brightness_down")])
    assert (presses[1].repeat, presses[1].missed) == (False, 1)


def test_pairing_gaps_are_not_missed_presses(valen):
    presses = feed(remotes.RemoteTracker(valen), valen, [(2950, "off"), (2961, "pairing"), (2964, "pairing")])
    assert [p.missed for p in presses] == [0, 0, 0]


def test_snapshot_restore_keeps_baselines(valen):
    first = remotes.RemoteTracker(valen)
    feed(first, valen, [(2280, "off")])
    second = remotes.RemoteTracker(valen)
    second.restore(json.loads(json.dumps(first.snapshot())))
    press = second.accept(message(valen, 2310, "on"), 100)
    assert press.missed == 2


def test_late_pairing_request_stays_in_its_session(valen):
    # Two boards: a pairing request 1-3 press numbers behind arrives during the session.
    presses = feed(remotes.RemoteTracker(valen), valen,
                   [(2580, "pairing"), (2591, "pairing"), (2587, "pairing")])
    assert [p.repeat for p in presses] == [False, True, True]
    assert presses[2].late is False
