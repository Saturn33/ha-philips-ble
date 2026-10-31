"""strings.json, en.json and ru.json stay complete and consistent."""

from __future__ import annotations

import json
import re

from .conftest import ROOT

PACKAGE = ROOT / "custom_components/ble_mesh_lights"
# Values that are the same in both languages (names).
SAME_IN_RUSSIAN = {"BLE Mesh Lights"}


def _flat(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flat(value, path))
        else:
            result[path] = value
    return result


def _load(path):
    return _flat(json.loads(path.read_text(encoding="utf-8")))


def test_en_equals_strings():
    assert _load(PACKAGE / "translations/en.json") == _load(PACKAGE / "strings.json")


def test_ru_has_every_key():
    strings, ru = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/ru.json")
    assert sorted(set(strings) - set(ru)) == []
    assert sorted(set(ru) - set(strings)) == []


def test_ru_is_translated():
    strings, ru = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/ru.json")
    same = [path for path, value in strings.items() if ru.get(path) == value and value not in SAME_IN_RUSSIAN]
    assert same == []


def test_ru_keeps_placeholders():
    strings, ru = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/ru.json")
    for path, value in strings.items():
        assert set(re.findall(r"\{\w+\}", value)) == set(re.findall(r"\{\w+\}", ru[path])), path


def test_no_key_references():
    """HA resolves [%key:...] only when it builds core's translations: a custom integration
    would show the reference itself."""
    for name in ("strings.json", "translations/en.json", "translations/ru.json", "translations/es.json"):
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert "[%key:" not in text, name


def test_ru_names_from_the_spec():
    ru = json.loads((PACKAGE / "translations/ru.json").read_text(encoding="utf-8"))
    entity = ru["entity"]
    assert entity["binary_sensor"]["power"]["name"] == "Питание"
    assert entity["button"]["brightness_up"]["name"] == "Ярче"
    assert entity["button"]["next_color_temperature"]["name"] == "Следующая температура"
    assert entity["sensor"]["transmitting_proxy"]["name"] == "Передающая плата"
    assert entity["event"]["button_press"]["name"] == "Нажатие кнопки"
    assert ru["device"]["remote"]["name"] == "Пульт {name} ({lamps})"
    assert ru["selector"]["after_outage"]["options"]["restore"] == "Вернуть прежнее состояние"
    assert ru["services"]["skip_sequence"]["name"] == "Перескочить счётчик"


def test_no_instruction_to_allow_device_actions():
    for name in ("strings.json", "translations/ru.json"):
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert "Allow the device to perform Home Assistant actions" not in text, name


VARIANT_STEPS = ("remote_pair", "pair_again", "settings", "test_failed")
VARIANT_ABORTS = ("reset_needed", "reset_needed_again")


def _keys(data, prefix=""):
    return set(_flat(data, prefix))


def test_every_profile_text_set_is_translated():
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    from _load import load

    profile = load("profile")
    profiles, errors = profile.load_profiles(profile.PROFILES_DIR)
    assert errors == []
    files = {
        name: json.loads((PACKAGE / name).read_text(encoding="utf-8"))
        for name in ("strings.json", "translations/en.json", "translations/ru.json", "translations/es.json")
    }
    reference = files["strings.json"]["config_subentries"]["lamp"]
    for loaded in profiles.values():
        for name, data in files.items():
            lamp = data["config_subentries"]["lamp"]
            for base in VARIANT_STEPS:
                variant = f"{base}_{loaded.texts}"
                assert variant in lamp["step"], (name, variant)
                assert _keys(lamp["step"][variant]) == _keys(reference["step"][f"{base}_valen"]), (
                    name,
                    variant,
                )
            for base in VARIANT_ABORTS:
                assert f"{base}_{loaded.texts}" in lamp["abort"], (name, base)
    # The base settings step (a lamp without a profile) asks the same as the variant.
    assert _keys(reference["step"]["settings"]["data"]) == _keys(reference["step"]["settings_valen"]["data"])


def test_ru_steps_have_no_english_hints():
    ru = json.loads((PACKAGE / "translations/ru.json").read_text(encoding="utf-8"))["config_subentries"][
        "lamp"
    ]
    texts = [str(v) for k, v in _flat(ru).items() if "_valen" in k]
    assert texts
    for text in texts:
        assert "Switch the light" not in text and "press and hold" not in text, text
        assert "Reconfigure" not in text, text
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
        content = (PACKAGE / name).read_text(encoding="utf-8")
        assert "{reset_hint}" not in content and "{remote_hint}" not in content, name


def test_pair_again_explains_the_blink_before_it_happens():
    """The check screen is shown for 2-3 s only: the blink is explained on the pair-again screen,
    the check screen says as little as when a lamp is added; the reset hint is its own line."""
    for name in ("strings.json", "translations/ru.json"):
        lamp = json.loads((PACKAGE / name).read_text(encoding="utf-8"))["config_subentries"]["lamp"]
        assert lamp["progress"]["again_test"] == lamp["progress"]["test"], name
        description = lamp["step"]["pair_again_valen"]["description"]
        assert "(" not in description, name
        assert "\n\nReset: " in description or "\n\nСброс: " in description, name


# Values that stay the same in Spanish (names, units, the remote's printed labels).
SAME_IN_SPANISH = {"BLE Mesh Lights", "Relax", "{name}", "No"}


def test_es_has_every_key():
    strings, es = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/es.json")
    assert sorted(set(strings) - set(es)) == []
    assert sorted(set(es) - set(strings)) == []


def test_es_is_translated():
    strings, es = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/es.json")
    same = [path for path, value in strings.items() if es.get(path) == value and value not in SAME_IN_SPANISH]
    assert same == []


def test_es_keeps_placeholders():
    strings, es = _load(PACKAGE / "strings.json"), _load(PACKAGE / "translations/es.json")
    for path, value in strings.items():
        assert set(re.findall(r"\{\w+\}", value)) == set(re.findall(r"\{\w+\}", es[path])), path


def test_es_steps_have_no_english_hints():
    es = json.loads((PACKAGE / "translations/es.json").read_text(encoding="utf-8"))["config_subentries"][
        "lamp"
    ]
    for path, text in _flat(es).items():
        if "_valen" in path:
            assert (
                "Switch the light" not in text and "press and hold" not in text and "Reconfigure" not in text
            ), path
