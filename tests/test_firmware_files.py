"""The ESPHome package and example node: the settings the integration relies on."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ESPHOME = ROOT / "esphome"


class _EsphomeLoader(yaml.SafeLoader):
    """SafeLoader that keeps ESPHome tags (!secret, !include, !lambda) as plain strings."""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: f"!{suffix} {node.value}")


def _load_yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_EsphomeLoader)


def test_package_settings():
    package = _load_yaml(ESPHOME / "ble-adv-tx.yaml")
    scan = package["esp32_ble_tracker"]["scan_parameters"]
    assert scan["interval"] == "1100ms"
    assert scan["window"] == "1100ms"
    assert package["ble_adv_proxy"]["pause_scan_on_send"] is True
    assert "bluetooth_proxy" not in package
    assert package["wifi"]["post_connect_roaming"] is False
    assert package["api"]["custom_services"] is True
    assert package["api"]["homeassistant_services"] is True
    assert package["substitutions"]["ble_adv_tx_version"] == "1"
    assert any(sensor.get("name") == "BLE TX package" for sensor in package["text_sensor"])


def test_component_has_pause_fix():
    source = (ESPHOME / "components/ble_adv_proxy/ble_adv_proxy.cpp").read_text(encoding="utf-8")
    assert "start_scan_if_idle" in source


def test_example_node_uses_repo():
    text = (ESPHOME / "example-node.yaml").read_text(encoding="utf-8")
    assert "github://Saturn33/ha-philips-ble/esphome/ble-adv-tx.yaml@" in text
    assert "path: esphome/components" in text
    assert "name: ble-proxy-tx-" in text


def test_local_node_builds_from_this_checkout():
    node = _load_yaml(ESPHOME / "local-node.yaml")
    assert node["packages"]["ble_adv_tx"] == "!include ble-adv-tx.yaml"
    (source,) = node["external_components"]
    assert source["source"] == {"type": "local", "path": "components"}
    assert source["components"] == ["ble_adv_proxy"]
    assert "github" not in (ESPHOME / "local-node.yaml").read_text(encoding="utf-8")


def test_local_node_is_the_example_node():
    """Only where the package comes from differs (the board names may be edited locally)."""
    local, example = _load_yaml(ESPHOME / "local-node.yaml"), _load_yaml(ESPHOME / "example-node.yaml")
    for node in (local, example):
        del node["packages"], node["external_components"]
        node["esphome"].pop("name"), node["esphome"].pop("friendly_name")
    assert local == example


def test_ci_compiles_the_local_node():
    workflow = (ROOT / ".github/workflows/firmware.yml").read_text(encoding="utf-8")
    assert "cp .github/esphome/secrets.yaml esphome/secrets.yaml" in workflow
    assert "esphome compile esphome/local-node.yaml" in workflow
    assert not (ROOT / ".github/esphome/ci-node.yaml").exists()


def test_personal_nodes_are_not_committed():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/esphome/ble-proxy-tx-*.yaml" in ignored


def test_ci_secrets_are_committed():
    # The firmware workflow copies these placeholders next to esphome/local-node.yaml.
    import subprocess

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", ".github/esphome/secrets.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked == [".github/esphome/secrets.yaml"]


def test_secrets_example_covers_example_node():
    import re

    node = (ESPHOME / "example-node.yaml").read_text(encoding="utf-8")
    needed = set(re.findall(r"!secret (\w+)", node))
    example = _load_yaml(ESPHOME / "secrets.yaml.example")
    assert needed and needed <= set(example)
    placeholders = {"MyNetwork", "wifi-password", "REPLACE_WITH_BASE64_KEY=", "fallback-ap-password"}
    assert set(example.values()) <= placeholders


def test_ci_api_key_is_a_readable_placeholder():
    import base64

    secrets = _load_yaml(ROOT / ".github" / "esphome" / "secrets.yaml")
    assert base64.b64decode(secrets["api_key"]) == b"CI-ONLY-PLACEHOLDER-KEY-32-BYTES"


def test_readme_covers_board_setup():
    readme = (ESPHOME / "README.md").read_text(encoding="utf-8")
    needles = (
        "secrets.yaml.example",
        "web.esphome.io",
        "BLE TX package",
        "post_connect_roaming",
        "fallback",
        "local-node.yaml",
        "cp example-node.yaml ble-proxy-tx-",
        "cp local-node.yaml ble-proxy-tx-",
    )
    for needle in needles:
        assert needle in readme, needle


def test_readme_does_not_require_allowing_actions():
    """The option only lets the board call HA actions; events and the board's own actions work
    without it (checked live and in HA's ESPHome source, 2026-10-28)."""
    readme = (ESPHOME / "README.md").read_text(encoding="utf-8")
    assert "enable **Allow the device to perform Home Assistant actions**" not in readme
    assert "is not needed" in readme
