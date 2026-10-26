"""``hermes auth add copilot``: GitHub device-code sign-in through the profile's auth_handler, driven
through core's real command; ``--api-key`` keeps core's paste path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.credential_pool import load_pool
from hermes_cli.auth_commands import auth_add_command

from github_copilot_plugin.copilot import credentials, setup


def _args(**kw):
    base = dict(provider="copilot", auth_type=None, label=None, api_key=None, portal_url=None,
                inference_url=None, client_id=None, scope=None, no_browser=False, timeout=None,
                insecure=False, ca_bundle=None, priority=None)
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def device_login(monkeypatch):
    calls = []

    def fake_login(**_kw):
        calls.append(1)
        return "ghu_deviceLoginTokenXXXXXXXXXXXX"

    monkeypatch.setattr(credentials, "copilot_device_code_login", fake_login)
    monkeypatch.setattr(setup, "_has_terminal", lambda: True)
    return calls


def test_auth_add_runs_device_login_and_pools_the_github_token(device_login, capsys):
    auth_add_command(_args())

    entries = load_pool("copilot").entries()
    assert device_login == [1]
    assert [(e.access_token, e.source, e.label) for e in entries] == [
        ("ghu_deviceLoginTokenXXXXXXXXXXXX", "manual:device_code", "github-device-1")]
    assert 'Added copilot credential #1: "github-device-1"' in capsys.readouterr().out


def test_api_key_flag_keeps_the_paste_path(device_login):
    auth_add_command(_args(api_key="github_pat_pastedTokenXXXXXXXXXX"))

    assert device_login == []
    assert [e.access_token for e in load_pool("copilot").entries()] == ["github_pat_pastedTokenXXXXXXXXXX"]


def test_no_terminal_never_starts_a_device_login(device_login, monkeypatch):
    monkeypatch.setattr(setup, "_has_terminal", lambda: False)

    assert setup.auth_handler("add", _args()) is False
    assert device_login == []


def test_failed_sign_in_saves_nothing(monkeypatch):
    monkeypatch.setattr(credentials, "copilot_device_code_login", lambda **_kw: None)
    monkeypatch.setattr(setup, "_has_terminal", lambda: True)

    with pytest.raises(SystemExit, match="did not complete"):
        auth_add_command(_args())
    assert load_pool("copilot").entries() == []


def test_pooled_device_login_token_is_exchanged_at_runtime(device_login, hermes_home, monkeypatch):
    from hermes_cli import config as _cfg
    from hermes_cli import runtime_provider as rp

    (hermes_home / "config.yaml").write_text("model:\n  provider: copilot\n  default: gpt-4.1\n")
    _cfg._LOAD_CONFIG_CACHE.clear()
    _cfg._RAW_CONFIG_CACHE.clear()
    auth_add_command(_args())
    monkeypatch.setattr(credentials, "get_copilot_api_token",
                        lambda tok: ("tid=exchanged;exp=1", None) if tok.startswith("ghu_device") else (tok, None))

    resolved = rp.resolve_runtime_provider(requested="copilot", target_model="gpt-4.1")

    assert resolved["provider"] == "copilot"
    assert resolved["api_key"] == "tid=exchanged;exp=1"
