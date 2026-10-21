"""Import the plugin as the package ``workflow_record_plugin`` (its directory name has a hyphen)
and give every test its own empty HERMES_HOME, so nothing reads or writes the real ~/.hermes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_package() -> None:
    if "workflow_record_plugin" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "workflow_record_plugin", PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["workflow_record_plugin"] = module
    spec.loader.exec_module(module)


_load_plugin_package()


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    return home
