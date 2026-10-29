"""Device profiles: loading, validation and the Philips Valen series profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

profile = load("profile")
mesh = load("mesh")

TESTS = Path(__file__).resolve().parent
VALEN = profile.PROFILES_DIR / "valen" / "profile.yaml"


@pytest.fixture(scope="module")
def valen():
    return profile.load_profile(VALEN)


def test_valen_loads(valen):
    assert valen.brightness_levels == (100, 80, 50, 30, 10, 5)
    assert valen.color_temps == (2200, 2700, 3000, 4000, 6500)
    assert valen.power_on == profile.Effect(power="on", color_temp=3000, level=100)
    assert len(valen.commands) == 11
    assert valen.remote.address == "mac_low15"


def test_payload_and_identify(valen):
    assert valen.commands["off"].payload(0x81) == bytes.fromhex("c81102810300")
    assert valen.identify(bytes.fromhex("c911028c0401ff41")) == "relax"
    assert valen.identify(bytes.fromhex("c81102fd030141")) == "on"
    assert valen.identify(bytes.fromhex("c8110200")) is None
    with pytest.raises(ValueError):
        valen.commands["off"].payload(0x7F)


def test_fixture_presses_identified(valen):
    codec = mesh.MeshCodec(valen.net_key, valen.app_key, valen.iv_index)
    seen = 0
    for line in (TESTS / "fixtures" / "valen_air.jsonl").read_text(encoding="utf-8").splitlines():
        for message in codec.decode(bytes.fromhex(json.loads(line)["raw"])):
            assert valen.identify(message.payload) in valen.commands, message
            seen += 1
    assert seen == 483


def test_bad_profile_reports_and_others_load():
    profiles, errors = profile.load_profiles(TESTS / "fixtures" / "profiles")
    assert "toggle" in profiles
    assert "broken" not in profiles
    assert len(errors) == 1
    assert "broken" in errors[0] and "party" in errors[0]


def test_repo_profiles_load():
    profiles, errors = profile.load_profiles(profile.PROFILES_DIR)
    assert "valen" in profiles
    assert errors == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("level: 50, scene: relax", "level: 42, scene: relax"),
        ("color_temp: 2700, level: 50", "color_temp: 2600, level: 50"),
        ("events: [\"on\"", "events: [party, \"on\""),
        ('"C8 11 02 TT 03 00"', '"C8 11 02 TT 03 ZZ"'),
        ('"C8 11 02 TT 03 00"', '"C8 11 TT 02 03 00"'),
    ],
)
def test_validation_errors(tmp_path, old, new):
    text = VALEN.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "profile.yaml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(profile.ProfileError) as error:
        profile.load_profile(path)
    assert str(error.value).startswith(str(path))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("[100, 80, 50, 30, 10, 5]", "[100, 80, 50, 30, 10, 5%]"),
        ("[2200, 2700, 3000, 4000, 6500]", "[2200, 2700, 2700, 3000, 4000, 6500]"),
        ('effect: {power: "off"}', "effect: []"),
        ('effect: {power: "off"}', "effect: 1"),
        ('effect: {brightness_step: 1}', 'effect: {brightness_step: "1"}'),
        ('effect: {brightness_step: 1}', "effect: {}"),
        ('effect: {power: "off"}', "effect: {color_temp: 3000}"),
        ('effect: {power: "off"}', 'effect: {power: "on", level: 100}'),
        ("sequence_step: 10", "sequence_step: 0"),
        ("requests: 20", "requests: 0"),
        ("active_timeout: 10", "active_timeout: -1"),
    ],
)
def test_unsupported_values_rejected(tmp_path, old, new):
    text = VALEN.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "profile.yaml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(profile.ProfileError):
        profile.load_profile(path)


def test_unexpected_error_in_one_profile_does_not_stop_others(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    text = VALEN.read_text(encoding="utf-8")
    (good / "profile.yaml").write_text(text, encoding="utf-8")
    (bad / "profile.yaml").write_text(text.replace("id: valen", "id: bad").replace("[100, 80", "[100, 8x"),
                                      encoding="utf-8")
    profiles, errors = profile.load_profiles(tmp_path)
    assert list(profiles) == ["valen"]
    assert len(errors) == 1 and "bad" in errors[0]
