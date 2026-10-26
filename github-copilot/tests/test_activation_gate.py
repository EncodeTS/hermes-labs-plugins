"""A GitHub token kept for other tools (GH_TOKEN / GITHUB_TOKEN) must not make Hermes call GitHub for a
home that never chose Copilot: no token exchange at api.github.com, no Copilot ``/models`` fetch.

Driven through core's real entry points: ``provider_model_ids`` is what the model-cache refresh thread
runs for every provider ``hermes status`` / the pickers list; ``resolve_api_key_provider_credentials``
and ``load_pool`` are the credential reads those surfaces make.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

AMBIENT = "gho_ambientTokenForOtherTools0000000000000"


@pytest.fixture
def github_calls(monkeypatch):
    """Every URL the process tries to open on a GitHub host; each attempt fails with a 401 (no retries)."""
    calls: list[str] = []
    real_open = urllib.request.OpenerDirector.open

    def fake_open(self, fullurl, *args, **kwargs):
        url = fullurl if isinstance(fullurl, str) else fullurl.full_url
        if any(host in url for host in ("github.com", "githubcopilot.com", "github.ai")):
            calls.append(url)
            raise urllib.error.HTTPError(url, 401, "blocked by test", {}, None)
        return real_open(self, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
    return calls


def _write_config(home, text: str) -> None:
    (home / "config.yaml").write_text(text)


def _touch_every_listing_path() -> tuple[list[str], dict]:
    from agent.credential_pool import load_pool
    from hermes_cli.auth import resolve_api_key_provider_credentials
    from hermes_cli.models import provider_model_ids

    models = provider_model_ids("copilot", force_refresh=True)
    creds = resolve_api_key_provider_credentials("copilot")
    load_pool("copilot")
    return models, creds


def test_ambient_github_token_never_reaches_github(hermes_home, monkeypatch, github_calls):
    monkeypatch.setenv("GH_TOKEN", AMBIENT)
    _write_config(hermes_home, "model:\n  provider: nous\n  default: Hermes-4-405B\n")

    models, creds = _touch_every_listing_path()

    assert github_calls == []
    assert "gpt-4.1" in models  # the curated list still fills the picker
    assert creds["api_key"] == AMBIENT  # present (raw), just never exchanged


_CHOSEN = {
    "model": "model:\n  provider: copilot\n  default: gpt-5.4\n",
    "alias": "model:\n  provider: github-copilot\n  default: gpt-5.4\n",
    "fallback": "model:\n  provider: nous\nfallback_providers:\n- provider: copilot\n  model: gpt-5.4\n",
    "delegation": "model:\n  provider: nous\ndelegation:\n  provider: copilot\n  model: gpt-5.4\n",
    "auxiliary": "model:\n  provider: nous\nauxiliary:\n  vision:\n    provider: copilot\n    model: gpt-4.1\n",
}


@pytest.mark.parametrize("config", _CHOSEN.values(), ids=_CHOSEN.keys())
def test_a_home_that_chose_copilot_exchanges(hermes_home, monkeypatch, github_calls, config):
    monkeypatch.setenv("GH_TOKEN", AMBIENT)
    _write_config(hermes_home, config)

    _touch_every_listing_path()

    assert any(url.endswith("/copilot_internal/v2/token") for url in github_calls)


def test_copilot_sign_in_counts_as_chosen(hermes_home, monkeypatch, github_calls):
    """``hermes auth add copilot`` (a manual pool row) and COPILOT_GITHUB_TOKEN both choose Copilot."""
    _write_config(hermes_home, "model:\n  provider: nous\n")
    (hermes_home / "auth.json").write_text(json.dumps({"version": 1, "credential_pool": {"copilot": [{
        "id": "abc123", "label": "github-device-1", "auth_type": "api_key", "priority": 0,
        "source": "manual:device_code", "access_token": AMBIENT}]}}))
    from github_copilot_plugin.copilot.activation import copilot_explicitly_configured

    assert copilot_explicitly_configured()
    (hermes_home / "auth.json").unlink()
    assert not copilot_explicitly_configured()
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", AMBIENT)
    assert copilot_explicitly_configured()


def test_a_user_started_copilot_flow_may_call_github(hermes_home, monkeypatch, github_calls):
    monkeypatch.setenv("GH_TOKEN", AMBIENT)
    _write_config(hermes_home, "model:\n  provider: nous\n")
    from github_copilot_plugin.copilot.activation import copilot_user_flow
    from hermes_cli.auth import resolve_api_key_provider_credentials

    with copilot_user_flow():
        resolve_api_key_provider_credentials("copilot")

    assert github_calls and github_calls[0].endswith("/copilot_internal/v2/token")
