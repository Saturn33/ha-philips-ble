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


def test_profile_guide_covers_every_field():
    import yaml

    profile = yaml.safe_load(_read("custom_components/ble_mesh_lights/profiles/valen/profile.yaml"))
    guide = _read("docs/PROFILES.md")
    keys = set(profile)
    for value in profile.values():  # network, power_on, remote, pairing, beacon
        if isinstance(value, dict) and value is not profile["commands"]:
            keys |= set(value)
    for command in profile["commands"].values():
        keys |= set(command) | set(command.get("effect", {}))
    for key in sorted(keys):
        assert f"`{key}`" in guide, key
    for needle in (
        "tools/README.md",
        "docs/PROTOCOL.md",
        "remote.json",
        "tests/fixtures/profiles",
        "new_light",
    ):
        assert needle in guide, needle
    assert "comes with the publishing version" not in guide


def test_no_references_to_private_documents():
    hits = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "grep",
            "-nE",
            r"docs/(superpowers|checks)|spec §|The spec ",
            "--",
            ":!.gitignore",
        ],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert [hit for hit in hits if not hit.startswith("tests/test_docs.py:")] == []


def test_architecture_records_the_pitfalls():
    architecture = _read("docs/ARCHITECTURE.md")
    for needle in ("heartbeat", "7 s", "six senders", "queue", "language", "colour temperature", "outage"):
        assert needle in architecture, needle
    for room in ("Street", "Patio", "Hall", "Terrace", "Kitchen", "Corridor", "Salon"):
        assert not re.search(rf"\b{room}\b", architecture, re.IGNORECASE), room


def test_contributing_has_setup_and_rules():
    contributing = _read("CONTRIBUTING.md")
    needles = (
        "requirements-test.txt",
        "requirements-test-ha.txt",
        'pytest tests/ha -q -o addopts=""',
        "node --test tests/card/*.mjs",
        "ruff check .",
        "tools/deploy_dev.sh",
        "personal data",
        "Saturn33/ha-philips-ble",
        "topics",
        "private",
    )
    for needle in needles:
        assert needle in contributing, needle


def test_agents_points_to_the_documents():
    agents = _read("AGENTS.md")
    for needle in (
        "docs/ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "ruff check .",
        "pytest -q",
        'pytest tests/ha -q -o addopts=""',
        "node --test tests/card/*.mjs",
    ):
        assert needle in agents, needle
