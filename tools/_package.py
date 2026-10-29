"""Import the integration's HA-free modules from the tools without running its __init__.py."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

PACKAGE = "ble_mesh_lights"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / PACKAGE


def load(name: str) -> ModuleType:
    if PACKAGE not in sys.modules:
        stub = ModuleType(PACKAGE)
        stub.__path__ = [str(PACKAGE_DIR)]
        sys.modules[PACKAGE] = stub
    return importlib.import_module(f"{PACKAGE}.{name}")


def load_profile(profile_id: str):
    profile = load("profile")
    profiles, errors = profile.load_profiles(profile.PROFILES_DIR)
    if profile_id not in profiles:
        sys.exit(f"Unknown profile {profile_id!r}. Available: {sorted(profiles)}. Errors: {errors}")
    return profiles[profile_id]
