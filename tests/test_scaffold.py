"""Repository scaffold: manifest, HACS metadata and the HA-free module loader."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_hacs():
    manifest = json.loads((ROOT / "custom_components/ble_mesh_lights/manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == "ble_mesh_lights"
    assert manifest["config_flow"] is True
    assert manifest["dependencies"] == ["bluetooth", "http", "websocket_api"]
    assert manifest["single_config_entry"] is True
    assert hacs["name"] == "BLE Mesh Lights"


def test_manifest_for_the_marketplace():
    manifest = json.loads((ROOT / "custom_components/ble_mesh_lights/manifest.json").read_text())
    assert manifest["version"] == "0.5.1"
    assert manifest["documentation"] == "https://github.com/Saturn33/ha-philips-ble"
    assert manifest["issue_tracker"] == "https://github.com/Saturn33/ha-philips-ble/issues"
    assert manifest["categories"] == ["lighting.bulbs_lamps", "buttons_remotes"]


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_old_repository_name_is_gone():
    old = "ha-philips-" + "valen-ble"  # split, or this file would match itself
    found = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", old], capture_output=True, text=True)
    assert found.returncode == 1, found.stdout or found.stderr  # 1 = no match; 2+ = git failed


def test_loader_skips_package_init(tmp_path):
    # A package whose __init__ needs Home Assistant, with two pure modules that
    # import each other relatively, like the real integration.
    package = tmp_path / "probe_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("import homeassistant  # noqa: F401\n")
    (package / "helper.py").write_text("VALUE = 42\n")
    (package / "probe.py").write_text("from .helper import VALUE\n\nANSWER = VALUE\n")

    module = load("probe", package="probe_pkg", package_dir=package)

    assert module.ANSWER == 42
    assert "homeassistant" not in sys.modules


def test_brand_images():
    """HA shows custom_components/<domain>/brand/ images for a custom integration."""
    brand = ROOT / "custom_components/ble_mesh_lights/brand"
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (brand / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        assert (width, height) == (size, size), name
