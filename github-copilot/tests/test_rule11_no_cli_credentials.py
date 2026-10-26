"""Catalog rule 11 (credentials only through Hermes): the plugin never runs ``gh`` and never reads the gh
CLI's or the Copilot CLI's token files, even when all of them are present and hold valid tokens. A GitHub
token reaches it only through Hermes: an env var (``.env`` / process env) or a ``hermes auth add`` row."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

import providers

_events: list[tuple[str, str]] = []
_recording = False


def _audit(event: str, args: tuple) -> None:
    if not _recording:
        return
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _events.append(("open", os.fsdecode(args[0])))
    elif event in ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn", "os.system"):
        _events.append(("spawn", repr(args[:2])))


sys.addaudithook(_audit)  # audit hooks cannot be removed; it only records while ``_recording`` is set

TOKEN = "gho_cliStoreTokenXXXXXXXXXXXXXXXX"


@pytest.fixture
def cli_credential_stores(tmp_path, monkeypatch):
    """Every store the core-bundled Copilot used to read, holding a valid token, plus a ``gh`` on
    PATH that would print one."""
    home = Path(os.environ["HOME"])
    files = {
        home / ".copilot" / "config.json": json.dumps({"copilotTokens": {"https://github.com:u": TOKEN}}),
        home / ".config" / "github-copilot" / "hosts.json": json.dumps({"github.com": {"oauth_token": TOKEN}}),
        home / ".config" / "github-copilot" / "apps.json": json.dumps({"github.com:Iv1": {"oauth_token": TOKEN}}),
        home / ".config" / "gh" / "hosts.yml": f"github.com:\n    oauth_token: {TOKEN}\n",
    }
    for path, body in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / ("gh.cmd" if os.name == "nt" else "gh")
    gh.write_text(f"@echo {TOKEN}\n" if os.name == "nt" else f"#!/bin/sh\necho {TOKEN}\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return [str(p) for p in files]


def _record(fn):
    global _recording
    _events.clear()
    _recording = True
    try:
        return fn()
    finally:
        _recording = False


def _assert_untouched(stores):
    opened = [path for kind, path in _events if kind == "open" and any(path == s for s in stores)]
    spawned = [args for kind, args in _events if kind == "spawn"]
    assert opened == [], f"read a CLI token file: {opened}"
    assert spawned == [], f"spawned a process: {spawned}"


def test_no_hermes_credential_means_no_token_despite_cli_stores(cli_credential_stores):
    from github_copilot_plugin.copilot import credentials
    from github_copilot_plugin.copilot.catalog import _resolve_copilot_catalog_api_key

    copilot = providers.get_provider_profile("copilot")
    acp = providers.get_provider_profile("copilot-acp")

    def run():
        return (
            credentials.resolve_copilot_token(),
            copilot.resolve_api_key_secret(),
            copilot.seed_pool_credentials(is_suppressed=lambda s: False, explicitly_configured=lambda: True),
            _resolve_copilot_catalog_api_key(),
            acp.external_auth_evidence(),
            copilot.account_status(),
        )

    token, secret, seeded, catalog_key, evidence, status = _record(run)

    assert token == ("", "")
    assert secret == ("", "")
    assert seeded == []
    assert catalog_key == ""
    assert evidence == (False, None)
    assert status["logged_in"] is False
    _assert_untouched(cli_credential_stores)


def test_env_token_still_resolves_without_touching_cli_stores(cli_credential_stores, monkeypatch):
    from github_copilot_plugin.copilot import credentials

    monkeypatch.setenv("GH_TOKEN", "gho_fromHermesEnvXXXXXXXXXXXXXXX")
    acp = providers.get_provider_profile("copilot-acp")

    token, evidence = _record(lambda: (credentials.resolve_copilot_token(), acp.external_auth_evidence()))

    assert token == ("gho_fromHermesEnvXXXXXXXXXXXXXXX", "GH_TOKEN")
    assert evidence == (True, "env: GH_TOKEN")
    _assert_untouched(cli_credential_stores)


def test_core_is_not_asked_to_fingerprint_cli_token_files():
    assert providers.get_provider_profile("copilot").catalog_credential_files == ()
