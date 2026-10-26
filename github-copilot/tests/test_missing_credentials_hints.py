"""A signed-out user is told how to sign in: core's "No usable credentials found" surfaces carry the
copilot profile's hint, and the Copilot CLI's ACP auth-required error names `copilot login`."""

from __future__ import annotations

import sys

import pytest

import providers

providers.get_provider_profile("copilot")  # discovery imports the plugin package
from github_copilot_plugin.copilot_acp.client import CopilotACPClient

SIGN_IN = "Run `hermes auth add copilot` to sign in."


def test_runtime_resolution_without_a_token_names_the_plugin_login():
    from hermes_cli.auth import AuthError
    from hermes_cli.runtime_provider import resolve_runtime_provider

    with pytest.raises(AuthError) as excinfo:
        resolve_runtime_provider(requested="copilot")
    message = str(excinfo.value)
    assert message.endswith(f". {SIGN_IN}")
    assert message.count("COPILOT_GITHUB_TOKEN") == 1  # core's own env list names it; the hint does not repeat it


def test_readiness_without_a_token_names_the_plugin_login():
    from hermes_cli.runtime_readiness import check_runtime_readiness

    verdict = check_runtime_readiness(
        "copilot", resolve=lambda: ("gpt-5.4", {"provider": "copilot", "api_key": "", "source": "none"}))
    assert verdict["ok"] is False
    assert verdict["error"] == f"No usable credentials found for copilot. {SIGN_IN}"


_SIGNED_OUT_CLI = """import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get("method") == "initialize":
        reply = {"result": {"protocolVersion": 1, "authMethods": [{"id": "github", "name": "GitHub"}]}}
    else:
        reply = {"error": {"code": -32000, "message": "Authentication required"}}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], **reply}), flush=True)
"""


def test_signed_out_copilot_cli_points_at_copilot_login(tmp_path):
    cli = tmp_path / "signed_out_copilot.py"
    cli.write_text(_SIGNED_OUT_CLI, encoding="utf-8")
    client = CopilotACPClient(command=sys.executable, args=[str(cli)], acp_cwd=str(tmp_path))
    with pytest.raises(RuntimeError) as excinfo:
        with client._session(30):
            pass
    assert str(excinfo.value) == ("Copilot ACP session/new failed: Authentication required "
                                  "Run `copilot login` to sign in to the GitHub Copilot CLI.")
