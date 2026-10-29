"""Command planner: the shortest chain of commands from what HA believes to what is wanted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

profile = load("profile")
state = load("state")
planner = load("planner")
S = state.LampState
T = planner.Target
UNKNOWN = state.UNKNOWN

TESTS = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def valen():
    return profile.load_profile(profile.PROFILES_DIR / "valen" / "profile.yaml")


@pytest.fixture(scope="module")
def toggle():
    return profile.load_profile(TESTS / "fixtures" / "profiles" / "toggle" / "profile.yaml")


def test_examples(valen):
    assert planner.plan(valen, S(True, 3000, 100), T(True, 2200, 100)) == ["energise", "color_temperature"]
    assert planner.plan(valen, S(True, 3000, 100), T(True, 3000, 30)) == ["brightness_down"] * 3
    assert planner.plan(valen, S(True, 3000, None), T(True, 3000, 30)) == ["bright"] + ["brightness_down"] * 3
    assert planner.plan(valen, S(False, 4000, 50), T(True, 2700, 50)) == ["relax"]
    assert planner.plan(valen, S(True, 6500, 50), T(True, 6500, 100)) == ["concentrate", "color_temperature"]


def test_worst_case_is_seven(valen):
    lengths = [
        len(planner.plan(valen, UNKNOWN, T(True, kelvin, level)))
        for kelvin in valen.color_temps
        for level in valen.brightness_levels
    ]
    assert max(lengths) <= 7
    assert len(planner.plan(valen, UNKNOWN, T(True, 2200, 5))) == 7


def test_plan_reaches_target(valen):
    for kelvin in valen.color_temps:
        for level in valen.brightness_levels:
            current = UNKNOWN
            for command in planner.plan(valen, current, T(True, kelvin, level)):
                current = state.apply(valen, current, command)
            assert (current.on, current.color_temp, current.level) == (True, kelvin, level)


def test_keep_fields(valen):
    assert planner.plan(valen, S(True, 4000, 100), T(True, None, 50)) == ["concentrate", "brightness_down"]
    assert planner.plan(valen, S(True, 4000, 80), T(True, None, 50)) == ["brightness_down"]
    assert planner.plan(valen, S(True, 4000, 100), T(False)) == ["off"]
    assert planner.plan(valen, S(None, None, None), T(False)) == ["off"]


def test_plain_power(valen):
    assert planner.plan(valen, S(False, 2200, 30), T(True)) == ["on"]
    assert planner.plan(valen, UNKNOWN, T(True)) == ["on"]
    assert planner.plan(valen, S(True, 2200, 30), T(True, level=30)) == []


def test_already_there(valen):
    assert planner.plan(valen, S(True, 3000, 100), T(True, 3000, 100)) == []
    assert planner.plan(valen, S(True, None, 100), T(True, 3000, 100)) != []


def test_unreachable_target_raises(valen):
    with pytest.raises(planner.PlanError):
        planner.plan(valen, S(True, 3000, 100), T(True, 3000, 42))
    with pytest.raises(planner.PlanError):
        planner.plan(valen, S(True, 3000, 100), T(True, 5000, 100))
    with pytest.raises(planner.PlanError):
        planner.plan(valen, S(True, 3000, 100), T(True, 2200, 5), max_length=1)


def test_toggle_profile_recovers(toggle):
    assert planner.plan(toggle, UNKNOWN, T(False)) == ["relax", "power"]
    assert planner.plan(toggle, S(True, 3000, 100), T(False)) == ["power"]


def test_plain_on_never_uses_a_scene(valen):
    # Power is off and some values are unknown: a plain turn_on must bring the lamp back as it was.
    assert planner.plan(valen, S(False, None, 80), T(True)) == ["on"]
    assert planner.plan(valen, S(False, 3000, None), T(True)) == ["on"]
    assert planner.plan(valen, S(None, 2200, None), T(True)) == ["on"]


def test_power_command(valen, toggle):
    assert planner.power_command(valen, True) == "on"
    assert planner.power_command(valen, False) == "off"
    assert planner.power_command(toggle, False) is None
