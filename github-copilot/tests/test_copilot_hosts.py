"""Copilot endpoint identity, owned by the copilot plugin's profile hooks.

The premium-request ``x-initiator: user`` header (issue #3040) goes only to the Copilot API and
GitHub Models hosts (the copilot profile's ``request_headers``), and host-based routing must match
URL *hostnames*, never substrings (``providers.profile_for_host``): a proxy path or a lookalike
domain that merely contains a Copilot host is not Copilot.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def copilot_profile():
    import providers

    profile = providers.get_provider_profile("copilot")
    assert profile is not None
    return profile


def _marks_user_turn(profile, url: str) -> bool:
    return "x-initiator" in profile.request_headers(base_url=url, user_initiated=True)


def _is_copilot_host(url: str) -> bool:
    from providers import profile_for_host

    profile = profile_for_host(url)
    return profile is not None and profile.name == "copilot"


class TestInitiatorHosts:
    """The x-initiator hook targets GitHub Copilot endpoints only."""

    def test_standard_copilot_url(self, copilot_profile):
        assert _marks_user_turn(copilot_profile, "https://api.githubcopilot.com") is True

    def test_github_models_url(self, copilot_profile):
        assert _marks_user_turn(copilot_profile, "https://models.github.ai/inference") is True

    def test_openrouter_url(self, copilot_profile):
        assert _marks_user_turn(copilot_profile, "https://openrouter.ai/api/v1") is False

    @pytest.mark.parametrize("plan", ["individual", "business", "enterprise"])
    def test_plan_hosts_from_the_token_exchange(self, copilot_profile, plan):
        # The exchange's ``endpoints.api`` is api.<plan>.githubcopilot.com, the host real accounts hit.
        assert _marks_user_turn(copilot_profile, f"https://api.{plan}.githubcopilot.com") is True

    @pytest.mark.parametrize("url", [
        "https://api.githubcopilot.com.evil.test/v1", "https://evil.test/api.individual.githubcopilot.com",
        "https://notgithubcopilot.com/v1",
    ])
    def test_lookalike_hosts(self, copilot_profile, url):
        assert _marks_user_turn(copilot_profile, url) is False


class TestTurnAttributionOnPlanHosts:
    """Through core's endpoint routing (``agent.provider_route_hooks.route_answer``, what the turn loop
    asks per API call): the first call of a user turn on an account host says ``user``; follow-ups send
    nothing extra, so the client's default ``x-initiator: agent`` stands."""

    URL = "https://api.individual.githubcopilot.com"

    @pytest.mark.parametrize("provider", ["copilot", "custom"])
    def test_first_call_is_user_follow_up_is_agent(self, provider):
        from agent.provider_route_hooks import route_answer

        first = route_answer("request_headers", provider=provider, base_url=self.URL, model="gpt-5.4",
                             user_initiated=True)
        follow_up = route_answer("request_headers", provider=provider, base_url=self.URL, model="gpt-5.4",
                                 user_initiated=False)
        client = route_answer("client_headers", provider=provider, base_url=self.URL, api_key="tid=x")
        assert first == {"x-initiator": "user"}
        assert not follow_up
        assert client["x-initiator"] == "agent"


@pytest.mark.parametrize("url, owned", [
    ("https://api.githubcopilot.com", True),
    ("https://api.business.githubcopilot.com", True),
    ("https://models.github.ai/inference", True),
    ("https://models.inference.ai.azure.com", True),
    ("https://api.githubcopilot.com.evil.test", False),
    ("https://evil.test/api.githubcopilot.com", False),
])
def test_models_probe_owns_the_copilot_host_family(url, owned, monkeypatch):
    """``/models`` probes (``hermes model`` validation) of a Copilot host read the account catalog with
    the editor headers; anything else falls back to core's generic probe."""
    from github_copilot_plugin.copilot import catalog

    monkeypatch.setattr(catalog, "_fetch_github_models", lambda api_key=None, timeout=5.0: ["gpt-5.4"])
    answer = catalog.CopilotCatalogMixin().probe_models(base_url=url, api_key="tid=x")
    assert (answer is not None) is owned
    if owned:
        assert answer[0] == ["gpt-5.4"]


def test_copilot_host_identity_is_host_anchored():
    assert _is_copilot_host("https://api.githubcopilot.com/v1") is True
    assert _is_copilot_host("https://proxy.test/api.githubcopilot.com/v1") is False
    assert _is_copilot_host("https://models.github.ai/inference") is True
    assert _is_copilot_host("https://models.github.ai.evil.com/v1") is False
