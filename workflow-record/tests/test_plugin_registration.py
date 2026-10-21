"""Plugin wiring through Hermes' real discovery path against a temp HERMES_HOME: the
``hermes record`` command and the replay skill exist only when the plugin is enabled, and the
registered command works end to end (manual recording -> file -> --list)."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
QUALIFIED_SKILL = "workflow-record:replay-recording"


def _install(hermes_home: Path, enabled: bool) -> None:
    dest = hermes_home / "plugins" / "workflow-record"
    shutil.copytree(PLUGIN_ROOT, dest, ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"))
    enabled_yaml = "    - workflow-record\n" if enabled else "    []\n"
    (hermes_home / "config.yaml").write_text("plugins:\n  enabled:\n" + enabled_yaml, encoding="utf-8")


def _manager():
    from hermes_cli.plugins import PluginManager

    pm = PluginManager()
    pm.discover_and_load()
    return pm


def _record_parser(pm) -> argparse.ArgumentParser:
    info = pm._cli_commands["record"]
    parser = argparse.ArgumentParser(prog="hermes record")
    info["setup_fn"](parser)
    parser.set_defaults(func=info["handler_fn"])
    return parser


@pytest.fixture
def enabled_manager(hermes_home):
    _install(hermes_home, enabled=True)
    return _manager()


def test_disabled_plugin_registers_nothing(hermes_home):
    _install(hermes_home, enabled=False)
    pm = _manager()
    assert "record" not in pm._cli_commands
    assert pm.find_plugin_skill(QUALIFIED_SKILL) is None


def test_enabled_plugin_registers_the_command_and_skill(enabled_manager):
    info = enabled_manager._cli_commands["record"]
    assert info["plugin"] == "workflow-record"
    skill_md = enabled_manager.find_plugin_skill(QUALIFIED_SKILL)
    assert skill_md is not None and skill_md.is_file()
    assert "name: replay-recording" in skill_md.read_text(encoding="utf-8")


def test_record_flags_parse(enabled_manager):
    parser = _record_parser(enabled_manager)
    args = parser.parse_args([])
    assert (args.slug, args.cdp, args.manual, args.list) == (None, None, False, False)
    args = parser.parse_args(["--slug", "checkout", "--manual"])
    assert (args.slug, args.manual) == ("checkout", True)
    with pytest.raises(SystemExit):
        parser.parse_args(["--manual", "--list"])


def test_manual_recording_then_list(enabled_manager, hermes_home, monkeypatch, capsys):
    parser = _record_parser(enabled_manager)

    args = parser.parse_args(["--list"])
    assert args.func(args) == 0
    assert "No recordings" in capsys.readouterr().out

    answers = iter(["https://shop.test/login", "open the login page", "sign in as alice", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    args = parser.parse_args(["--manual", "--slug", "shop login"])
    assert args.func(args) == 0
    out = capsys.readouterr().out

    saved = sorted((hermes_home / "recordings").glob("shop-login-*.json"))
    assert len(saved) == 1
    doc = json.loads(saved[0].read_text(encoding="utf-8"))
    assert doc["url"] == "https://shop.test/login"
    assert [s["text"] for s in doc["steps"]] == ["open the login page", "sign in as alice"]
    assert f"/learn the browser workflow recorded in {saved[0]}" in out
    assert QUALIFIED_SKILL in out

    args = parser.parse_args(["--list"])
    assert args.func(args) == 0
    assert saved[0].name in capsys.readouterr().out


def test_cdp_mode_without_a_browser_fails_cleanly(enabled_manager, capsys):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    parser = _record_parser(enabled_manager)
    args = parser.parse_args(["--cdp", f"http://127.0.0.1:{port}"])
    assert args.func(args) == 1
    out = capsys.readouterr().out
    assert "No browser to record" in out and "--manual" in out
