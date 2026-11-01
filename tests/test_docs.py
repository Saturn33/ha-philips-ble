"""The published documentation: what the user and developer guides must cover, and links that resolve."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_MD = subprocess.run(
    ["git", "-C", str(ROOT), "ls-files", "*.md"], capture_output=True, text=True, check=True
).stdout.split()
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_is_a_user_guide():
    readme = _read("README.md")
    needles = (
        "web.esphome.io",
        "ESPHome Device Builder",
        "github://Saturn33/ha-philips-ble/esphome/ble-adv-tx.yaml@v0.5.0",
        "Settings → Marketplace",
        "Custom repositories",
        "https://my.home-assistant.io/redirect/hacs_repository/?owner=Saturn33&repository=ha-philips-ble"
        "&category=integration",
        "https://my.home-assistant.io/redirect/config_flow_start/?domain=ble_mesh_lights",
        "Factory-reset",
        "Pair again",
        "custom:ble-mesh-remote-card",
        "docs/PROFILES.md",
        "docs/KNOWN_ISSUES.md",
        "esphome/README.md",
        "Ctrl+Shift+R",
        "jphelan82/ha-philips-skylight",
        "NicoIIT/esphome-ble_adv_proxy",
        "MIT",
    )
    for needle in needles:
        assert needle in readme, needle
    assert "Work in progress" not in readme
    assert "![" not in readme.replace("[![", "")  # badges only, no screenshots in 0.5.0


def test_readme_minimum_ha_matches_hacs():
    hacs = json.loads(_read("hacs.json"))
    assert f"Home Assistant {hacs['homeassistant']}" in _read("README.md")


def test_markdown_links_resolve():
    broken = []
    for name in PUBLISHED_MD:
        for target in LINK.findall(_read(name)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if not ((ROOT / name).parent / path).exists():
                broken.append(f"{name}: {target}")
    assert broken == []
