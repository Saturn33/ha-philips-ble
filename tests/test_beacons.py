"""Lamp beacons: whose they are, quiet mode, and when a lamp has lost or regained power."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

profile = load("profile")
beacons = load("beacons")
mesh = load("mesh")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "valen_air.jsonl"
L1 = "E8:26:CF:54:65:AD"
ACTIVE = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8000001000000000000")
QUIET = bytes.fromhex("182b0000ffffffff3101ad6554cf26e8010001000000000000")


@pytest.fixture(scope="module")
def valen():
    return profile.load_profile(profile.PROFILES_DIR / "valen" / "profile.yaml")


def _fixture(file: str | None = None):
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if file is None or record["file"] == file:
            yield record


def test_active_beacon(valen):
    assert beacons.parse_beacon(valen, ACTIVE) == beacons.Beacon(L1, quiet=False)


def test_quiet_beacon(valen):
    assert beacons.parse_beacon(valen, QUIET) == beacons.Beacon(L1, quiet=True)


def test_not_a_beacon(valen):
    remote = next(r["raw"] for r in _fixture() if r["mac"] != L1)
    for raw in (remote, "", "020106"):
        assert beacons.parse_beacon(valen, bytes.fromhex(raw)) is None


def test_connectable_advert_is_a_beacon(valen):
    # In active mode HA keeps this advert (service data 0x1827, same UUID) as the lamp's last raw advert.
    adverts = [r["raw"] for r in _fixture() if r["mac"] == L1 and r["raw"].startswith("0201")]
    assert adverts
    for raw in adverts:
        assert beacons.parse_beacon(valen, bytes.fromhex(raw)) == beacons.Beacon(L1, quiet=False)


def test_watched_lamp_never_seen_counts_as_lost(valen):
    tracker = beacons.PresenceTracker(valen)
    tracker.watch(L1, 0)
    assert tracker.present(L1) is None
    assert tracker.expired(100) == []  # it may be in quiet mode: wait a whole quiet timeout
    assert tracker.expired(191) == [L1]
    assert tracker.present(L1) is False
    assert tracker.seen(beacons.Beacon(L1, quiet=False), 200) == "restored"


def test_fixture_lamp(valen):
    parsed = [beacons.parse_beacon(valen, bytes.fromhex(r["raw"])) for r in _fixture() if r["mac"] == L1
              and r["raw"].startswith("182b")]
    assert parsed and all(b is not None and b.mac == L1 for b in parsed)
    assert any(b.quiet for b in parsed) and any(not b.quiet for b in parsed)


def test_lost_timeout(valen):
    assert beacons.lost_timeout(valen, beacons.Beacon(L1, quiet=False)) == 10
    assert beacons.lost_timeout(valen, beacons.Beacon(L1, quiet=True)) == 190


def test_quiet_lamp_after_restart(valen):
    tracker = beacons.PresenceTracker(valen)
    assert tracker.seen(beacons.Beacon(L1, quiet=True), 0) is None
    assert tracker.expired(59) == []
    assert tracker.seen(beacons.Beacon(L1, quiet=True), 60) is None
    assert tracker.present(L1) is True


def test_loss_and_restore(valen):
    tracker = beacons.PresenceTracker(valen)
    assert tracker.present(L1) is None
    tracker.seen(beacons.Beacon(L1, quiet=False), 0)
    assert tracker.expired(5) == []
    assert tracker.expired(11) == [L1]
    assert tracker.expired(12) == []  # reported once per loss
    assert tracker.present(L1) is False
    assert tracker.seen(beacons.Beacon(L1, quiet=False), 30) == "restored"
    assert tracker.present(L1) is True


def test_quiet_beacon_after_loss_is_not_a_restore(valen):
    tracker = beacons.PresenceTracker(valen)
    tracker.seen(beacons.Beacon(L1, quiet=True), 0)
    assert tracker.expired(191) == [L1]
    assert tracker.seen(beacons.Beacon(L1, quiet=True), 180) is None
    assert tracker.present(L1) is True


def test_06c_replay(valen):
    tracker = beacons.PresenceTracker(valen)
    losses, restores, quiet_times = 0, 0, []
    for record in _fixture("06c-pair.jsonl"):
        if record["mac"] != L1:
            continue
        beacon = beacons.parse_beacon(valen, bytes.fromhex(record["raw"]))
        if beacon is None:
            continue
        losses += len(tracker.expired(record["t"]))
        restores += tracker.seen(beacon, record["t"]) == "restored"
        if beacon.quiet:
            quiet_times.append(record["t"])
    gaps = [b - a for a, b in zip(quiet_times, quiet_times[1:], strict=False)]
    assert max(gaps) > 120  # a whole quiet-mode burst was missed
    assert (losses, restores) == (1, 1)


def test_two_missed_quiet_bursts_are_not_a_loss(valen):
    # Quiet-mode bursts come once a minute and about 1 in 20 is missed (06c): two misses in a
    # row leave a 180 s gap, which must not reset the lamp to on/100 %/3000 K.
    tracker = beacons.PresenceTracker(valen)
    tracker.seen(beacons.Beacon(L1, quiet=True), 0)
    assert tracker.expired(181) == []
    assert tracker.seen(beacons.Beacon(L1, quiet=True), 181) is None
