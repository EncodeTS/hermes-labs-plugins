"""GitHub Copilot ACP setup and Accounts-card surfaces: the ``hermes model`` flow and the
Desktop card that points at the Copilot CLI's own login. Reached through ``SETUP_FIELDS``."""

from __future__ import annotations

import contextlib
from typing import Any


def _cli_status() -> tuple[dict[str, Any], Exception | None]:
    """Hermes' launch details for the Copilot CLI (command, base_url), or the reason it can't run."""
    from hermes_cli.auth import resolve_external_process_provider_credentials
    try:
        return resolve_external_process_provider_credentials("copilot-acp"), None
    except Exception as exc:  # health: allow BLE001 -- missing CLI (AuthError) is a status, not a failure
        return {}, exc


def account_status() -> dict[str, Any]:
    """Status for copilot-acp. ``logged_in`` only on positive evidence (a supported env token); the CLI
    keeps its own login, which Hermes does not read (catalog rule 11), so the unverified state reads
    "managed by the Copilot CLI" — never signed out."""
    from ..copilot.credentials import copilot_cli_auth_evidence
    creds, error = _cli_status()
    verified, auth_source = copilot_cli_auth_evidence()
    configured = error is None
    if verified:
        source_label = auth_source or "Copilot credentials detected"
    elif configured:
        source_label = f"Managed by the GitHub Copilot CLI ({creds.get('command') or 'copilot'})"
    else:
        source_label = "GitHub Copilot CLI not found on PATH"
    return {
        "logged_in": verified, "source": "copilot_cli", "source_label": source_label, "token_preview": None,
        "expires_at": None, "has_refresh_token": False, "configured": configured,
    }


def model_flow_copilot_acp(config, current_model="", args=None):
    """GitHub Copilot ACP flow using the local Copilot CLI (a user-started Copilot flow: the picker may
    read the account catalog)."""
    from ..copilot.activation import copilot_user_flow

    del config, args
    with copilot_user_flow():
        _model_flow_copilot_acp(current_model)


def _model_flow_copilot_acp(current_model):
    from hermes_cli.model_setup import persist_model_selection
    from ..copilot import setup as copilot_setup
    from ..copilot.helpers import say

    provider_id = "copilot-acp"
    creds, error = _cli_status()
    resolved_command = creds.get("command") or "copilot"
    effective_base = creds.get("base_url") or "acp://copilot"

    say("  GitHub Copilot ACP delegates Hermes turns to `copilot --acp`.",
        "  Hermes currently starts its own ACP subprocess for each request.",
        "  Hermes uses your selected model as a hint for the Copilot ACP session.",
        f"  Command: {resolved_command}", f"  Backend marker: {effective_base}", "")
    if error is not None:
        say(f"  ⚠ {error}", "  Set HERMES_COPILOT_ACP_COMMAND or COPILOT_CLI_PATH if Copilot CLI is installed elsewhere.")
        return

    catalog_api_key = ""
    with contextlib.suppress(Exception):
        catalog_api_key = copilot_setup.copilot_api_key()[0]
    _catalog, catalog_ids, _normalize = copilot_setup._copilot_catalog(catalog_api_key)
    selected = copilot_setup.pick_model_or_prompt(
        copilot_setup._copilot_model_list(catalog_ids), "Model name: ", current_model=_normalize(current_model),
        confirm_provider=provider_id, confirm_base_url=effective_base, confirm_api_key=catalog_api_key)
    if not selected:
        print("No change.")
        return
    selected = _normalize(selected)
    persist_model_selection(selected, provider_id, base_url=effective_base, api_mode="chat_completions")
    print(f"Default model set to: {selected} (via GitHub Copilot ACP)")


SETUP_FIELDS: dict[str, Any] = {
    "model_setup_flow": model_flow_copilot_acp,
    # `copilot login` is the non-interactive subcommand; `copilot /login` is not valid
    # (slash-commands only exist inside an interactive session).
    "account_card": {"name": "GitHub Copilot (ACP)", "flow": "external", "cli_command": "copilot login",
                     "docs_url": "https://docs.github.com/en/copilot"},
    "account_status": account_status,
}
