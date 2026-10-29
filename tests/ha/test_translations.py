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
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
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
