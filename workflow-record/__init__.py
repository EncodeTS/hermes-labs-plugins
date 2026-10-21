"""workflow-record: ``hermes record`` captures a browser workflow you demonstrate once.

Registers, through the public plugin surface only:

* ``hermes record`` (``ctx.register_cli_command``): attach to a Chromium-family browser over
  CDP, capture clicks / field values / Enter presses / navigations with passwords masked
  in-page, save ``<HERMES_HOME>/recordings/<slug>-<ts>.json``.
* the ``workflow-record:replay-recording`` skill (``ctx.register_skill``): how to read a
  recording and author a skill that replays it with the browser tools, wiring every
  ``{SECRET:name}`` placeholder to the user's vault or secret references.
"""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "replay-recording"
SKILL_DESCRIPTION = "Turn a hermes record browser recording into a skill."


def register(ctx) -> None:
    # Imported here, not at module top: pytest imports this file bare (no parent package)
    # when it collects the plugin directory.
    from .cli import DESCRIPTION, HELP, handle, setup_parser

    ctx.register_cli_command(
        "record", help=HELP, setup_fn=setup_parser, handler_fn=handle, description=DESCRIPTION,
    )
    ctx.register_skill(
        SKILL_NAME, Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md",
        description=SKILL_DESCRIPTION,
    )
