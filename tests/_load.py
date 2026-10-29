"""Load the integration's HA-free modules without running its __init__.py.

The package __init__ needs Home Assistant, but the protocol and state modules do
not. A stub package module is registered under the package name, with __path__
pointing at the package directory and no __init__ executed, so the pure modules
can import each other relatively (``from .state import apply``) exactly as they
do inside Home Assistant.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

PACKAGE = "ble_mesh_lights"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / PACKAGE


def load(name: str, *, package: str = PACKAGE, package_dir: Path = PACKAGE_DIR) -> ModuleType:
    if package not in sys.modules:
        stub = ModuleType(package)
        stub.__path__ = [str(package_dir)]
        sys.modules[package] = stub
    return importlib.import_module(f"{package}.{name}")
