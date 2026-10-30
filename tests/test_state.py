"""Lamp state model: how each command changes what HA believes about a lamp."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

profile = load("profile")
state = load("state")
S = state.LampState

TESTS = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def valen():
    return profile.load_profile(profile.PROFILES_DIR / "valen" / "profile.yaml")


@pytest.fixture(scope="module")
def toggle():
    return profile.load_profile(TESTS / "fixtures" / "profiles" / "toggle" / "profile.yaml")


def test_scene_sets_everything(valen):
    assert state.apply(valen, S(False, None, None), "nightlight") == S(True, 2700, 10)


def test_select_cycles_and_resets_level(valen):
    assert state.apply(valen, S(True, 6500, 30), "color_temperature") == S(True, 2200, 100)
    assert state.apply(valen, S(True, 3000, 5), "color_temperature") == S(True, 4000, 100)
    assert state.apply(valen, S(True, None, 30), "color_temperature") == S(True, None, 100)


def test_brightness_steps_clamp(valen):
    assert state.apply(valen, S(True, 3000, 100), "brightness_up") == S(True, 3000, 100)
    assert state.apply(valen, S(True, 3000, 5), "brightness_down") == S(True, 3000, 5)
    assert state.apply(valen, S(True, 3000, 80), "brightness_down") == S(True, 3000, 50)
    assert state.apply(valen, S(True, 3000, None), "brightness_down") == S(True, 3000, None)


def test_dimming_clears_scene(valen):
    assert state.apply(valen, S(True, 2700, 50), "brightness_up") == S(True, 2700, 80)


def test_off_ignores_dimming(valen):
    assert state.apply(valen, S(False, 3000, 50), "brightness_up") == S(False, 3000, 50)
    assert state.apply(valen, S(False, 3000, 50), "color_temperature") == S(False, 3000, 50)


def test_on_keeps_previous_settings(valen):
    assert state.apply(valen, S(False, 2200, 30), "on") == S(True, 2200, 30)
    assert state.apply(valen, S(True, 2200, 30), "off") == S(False, 2200, 30)


def test_command_without_effect_changes_nothing(valen):
    assert state.apply(valen, S(True, 3000, 50), "pairing") == S(True, 3000, 50)


def test_power_restored(valen):
    assert state.power_restored(valen) == S(True, 3000, 100)


def test_apply_press_after_gap(valen):
    start = S(True, 3000, 50)
    # A missed press could have been SELECT or a dimming step: both values become unknown.
    assert state.apply_press(valen, start, "brightness_down", missed=1) == (S(True, None, None), True)
    assert state.apply_press(valen, start, "color_temperature", missed=1) == (S(True, None, 100), True)
    assert state.apply_press(valen, start, "relax", missed=2) == (S(True, 2700, 50), False)
    assert state.apply_press(valen, start, "off", missed=1) == (S(False, None, None), True)
    assert state.apply_press(valen, start, "brightness_down", missed=0) == (
        state.apply(valen, start, "brightness_down"),
        False,
    )


def test_toggle_profile(toggle):
    assert state.apply(toggle, S(True, 3000, 100), "power").on is False
    assert state.apply(toggle, state.UNKNOWN, "power").on is None


def test_nearest(valen):
    assert state.nearest_level(valen, 60) == 50
    assert state.nearest_level(valen, 3) == 5
    assert state.nearest_color_temp(valen, 3400) == 3000
    assert state.nearest_color_temp(valen, 3600) == 4000
    assert state.nearest_color_temp(valen, 9000) == 6500


def test_unknown_power_makes_relative_commands_uncertain(valen):
    # An off lamp ignores dimming and SELECT; with power unknown, HA cannot tell whether it moved.
    assert state.apply(valen, S(None, 3000, 50), "brightness_up") == S(None, 3000, None)
    assert state.apply(valen, S(None, 3000, 50), "color_temperature") == S(None, None, None)


@pytest.mark.parametrize(
    ("current", "requested", "expected"),
    [
        (50, 60, 80),  # a little up from a step: the nearest is the current one, so one step up
        (50, 85, 80),  # a larger request: the nearest step, not one beyond it
        (10, 40, 30),
        (50, 95, 100),
        (50, 50, 50),  # exactly a step: that step
        (50, 80, 80),
        (50, 45, 30),  # a little down: the next step down
        (50, 7, 5),
        (50, 3, 5),  # below the lowest step
        (5, 1, 5),
        (100, 100, 100),
        (None, 60, 50),  # current unknown: the nearest step
    ],
)
def test_snap_level(valen, current, requested, expected):
    assert state.snap_level(valen, requested, current) == expected


@pytest.mark.parametrize(
    ("current", "requested", "expected"),
    [
        (3000, 3100, 4000),
        (3000, 2900, 2700),
        (2200, 3300, 3000),  # nearest in mireds
        (3000, 3000, 3000),
        (2200, 2000, 2200),
        (6500, 7000, 6500),
        (None, 3300, 3000),
    ],
)
def test_snap_color_temp(valen, current, requested, expected):
    assert state.snap_color_temp(valen, requested, current) == expected


@pytest.mark.parametrize(
    ("lamp", "scene"),
    [
        (S(True, 2700, 50), "relax"),
        (S(True, 3000, 100), "bright"),
        (S(True, 4000, 80), "concentrate"),
        (S(True, 6500, 50), "energise"),
        (S(True, 2700, 10), "nightlight"),
        (S(True, 2700, 30), None),
        (S(False, 2700, 50), None),
    ],
)
def test_scene_found_by_parameters(valen, lamp, scene):
    assert state.scene_of(valen, lamp) == scene


def test_no_scene_when_level_unknown(valen):
    assert state.scene_of(valen, S(True, 2700, None)) is None
    assert state.scene_of(valen, S(None, 2700, 50)) is None


def test_scene_back_after_dimming(valen):
    lamp = state.apply(valen, S(False, None, None), "relax")
    lamp = state.apply(valen, lamp, "brightness_up")
    assert state.scene_of(valen, lamp) is None
    lamp = state.apply(valen, lamp, "brightness_down")
    assert state.scene_of(valen, lamp) == "relax"
