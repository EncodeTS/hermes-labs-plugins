"""When the plugin may talk to GitHub: only for a home that chose Copilot, or while the user is in a
Copilot flow they started.

GH_TOKEN / GITHUB_TOKEN are general-purpose GitHub variables (gh, git hosting tools, CI), so their
presence alone is not a choice of Copilot. Without this gate, any surface that lists providers
(``hermes status``, the model picker, Desktop's model options) warmed the Copilot catalog for every
home holding such a token: the token went to the Copilot exchange at api.github.com and the result
fetched Copilot ``/models``. Core's ``is_provider_explicitly_configured`` cannot answer this for
Copilot (it counts every profile ``env_vars`` entry, GH_TOKEN included), so the plugin decides.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# The provider ids that select this plugin's Copilot API profile (name + aliases in copilot/__init__.py).
COPILOT_PROVIDER_IDS = frozenset({"copilot", "github-copilot", "github-models", "github-model", "github"})
# Written only by Hermes' own Copilot sign-in (``hermes model``, Desktop) or pasted for Copilot.
_COPILOT_ONLY_ENV_VAR = "COPILOT_GITHUB_TOKEN"
# Pool rows a user created through a Hermes flow (``hermes auth add copilot``, with or without --api-key).
_EXPLICIT_POOL_SOURCES = frozenset({"manual", "device_code"})

_user_flow: contextvars.ContextVar[bool] = contextvars.ContextVar("copilot_user_flow", default=False)


@contextlib.contextmanager
def copilot_user_flow() -> Iterator[None]:
    """Mark a Copilot flow the user started (``hermes model`` → Copilot, ``hermes auth add copilot``):
    GitHub calls inside it are the user's request even before anything is configured."""
    token = _user_flow.set(True)
    try:
        yield
    finally:
        _user_flow.reset(token)


def _selects(slot: Any) -> bool:
    return isinstance(slot, dict) and str(slot.get("provider") or "").strip().lower() in COPILOT_PROVIDER_IDS


def _chain(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _config_slots(cfg: dict) -> Iterator[Any]:
    """Every config.yaml slot that can route a request to a provider."""
    yield cfg.get("model")
    for key in ("fallback_model", "fallback_providers"):
        yield from _chain(cfg.get(key))
    delegation = cfg.get("delegation")
    if isinstance(delegation, dict):
        yield delegation
        yield from _chain(delegation.get("fallback_providers"))
    aux = cfg.get("auxiliary")
    if isinstance(aux, dict):
        yield from aux.values()
    moa = cfg.get("moa")
    if isinstance(moa, dict):
        presets = moa.get("presets")
        for block in (moa, *(presets.values() if isinstance(presets, dict) else ())):
            if isinstance(block, dict):
                yield from _chain(block.get("reference_models") or [])
                yield block.get("aggregator")


def _config_selects_copilot() -> bool:
    from hermes_cli.config import load_config

    cfg = load_config() or {}
    return any(_selects(slot) for slot in _config_slots(cfg))


def _pool_has_explicit_row() -> bool:
    from agent.credential_pool import read_credential_pool

    for entry in read_credential_pool("copilot") or ():
        source = str(entry.get("source") or "").strip().lower() if isinstance(entry, dict) else ""
        if source in _EXPLICIT_POOL_SOURCES or source.startswith("manual:"):
            return True
    return False


def _copilot_env_token_set() -> bool:
    from agent.secret_scope import get_secret

    return bool((get_secret(_COPILOT_ONLY_ENV_VAR) or "").strip())


_CHECKS = (_copilot_env_token_set, _config_selects_copilot, _pool_has_explicit_row)


def copilot_explicitly_configured() -> bool:
    """True when this home chose Copilot: COPILOT_GITHUB_TOKEN, a config slot naming copilot (model,
    fallback, delegation, auxiliary task, MoA), or a pool row from ``hermes auth add copilot``."""
    for check in _CHECKS:
        try:
            if check():
                return True
        except Exception as exc:  # a broken config/auth store reads as "not chosen": no GitHub call
            logger.debug("copilot explicit-config check %s failed: %s", check.__name__, exc)
    return False


def github_calls_allowed() -> bool:
    """Whether the plugin may exchange a token at GitHub or fetch the Copilot catalog right now."""
    return _user_flow.get() or copilot_explicitly_configured()
