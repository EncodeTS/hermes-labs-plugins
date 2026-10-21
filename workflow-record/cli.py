"""``hermes record``: demonstrate a browser workflow once, save it as a recording JSON."""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .recording import build_recording, list_recordings, recordings_dir, save_recording

SKILL_REF = "workflow-record:replay-recording"

HELP = "Record a browser workflow over CDP; /learn turns it into a skill"
DESCRIPTION = (
    "Attach to your Chromium-family browser over CDP and capture clicks, field values\n"
    "(passwords masked as {SECRET:name} placeholders inside the page), Enter presses and\n"
    "navigations in the active tab until Ctrl-C. The recording is saved under\n"
    "HERMES_HOME/recordings/; the command then prints the /learn line that turns it into a\n"
    "replayable skill.\n\n"
    "Endpoint: --cdp, else BROWSER_CDP_URL, else browser.cdp_url, else http://127.0.0.1:9222\n"
    "(the port /browser connect launches Chrome on)."
)


def setup_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", default=None, help="Name stem for the recording file (default: 'recording').")
    parser.add_argument("--cdp", default=None, metavar="URL",
                        help="CDP endpoint (http://host:port or ws://.../devtools/browser/<id>).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--manual", action="store_true",
                      help="No CDP: perform the steps in any browser and type what you did, one per line.")
    mode.add_argument("--list", action="store_true", help="List saved recordings and exit.")


def handle(args: argparse.Namespace) -> int:
    if getattr(args, "list", False):
        return _cmd_list()
    if getattr(args, "manual", False):
        return _run_manual(args)
    return _run_cdp(args)


def _print_next_step(path: Path, count: int) -> None:
    print(f"\nSaved {count} steps -> {path}")
    print("Review it, then turn it into a skill. In a Hermes chat, run:")
    print(f"  /learn the browser workflow recorded in {path} (follow the {SKILL_REF} skill)")


def _cmd_list() -> int:
    recs = list_recordings()
    if not recs:
        print(f"No recordings in {recordings_dir()}. Start one with: hermes record --slug my-flow")
        return 0
    print(f"Recordings in {recordings_dir()}:\n")
    for r in recs:
        if "error" in r:
            print(f"  {r['name']}  (unreadable)")
        else:
            print(f"  {r['name']}  {r['steps']} steps  {r.get('url', '')}")
    print(f"\nTurn one into a skill:  /learn the browser workflow recorded in <path> (follow the {SKILL_REF} skill)")
    return 0


def _run_manual(args: argparse.Namespace) -> int:
    print("Manual recording: perform the workflow in any browser and describe each step here,")
    print("one per line. Do not type passwords; write {SECRET:name} where one goes.")
    print("Empty line or Ctrl-D to finish.\n")
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        url = input("Start URL (optional): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        url = ""
    events: List[Dict[str, Any]] = []
    while True:
        try:
            line = input(f"step {len(events) + 1}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        events.append({"t": float(len(events) + 1), "type": "manual", "text": line})
    if not events:
        print("Nothing recorded.")
        return 1
    recording = build_recording(url, started_at, events)
    path = save_recording(recording, args.slug or "manual")
    _print_next_step(path, len(recording["steps"]))
    return 0


def _run_cdp(args: argparse.Namespace) -> int:
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("CDP recording needs the 'websockets' package (hermes plugins update workflow-record")
        print("installs it). Or record without CDP: hermes record --manual")
        return 1

    from .cdp import record_over_cdp, resolve_endpoint

    ws_url, source, error = resolve_endpoint(args.cdp)
    if not ws_url:
        print(f"No browser to record ({source}: {error}).")
        print("Start Chrome with remote debugging (in a chat, /browser connect launches it on :9222),")
        print("or pass --cdp URL / set browser.cdp_url. Without CDP: hermes record --manual")
        return 1

    events: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {"url": "", "ready": False}
    stop = threading.Event()
    errors: List[BaseException] = []
    started_at = datetime.now(timezone.utc).isoformat()

    def _runner() -> None:
        try:
            asyncio.run(record_over_cdp(ws_url, events, stop, state))
        except BaseException as exc:  # noqa: BLE001 - surfaced to the user below
            errors.append(exc)

    thread = threading.Thread(target=_runner, name="workflow-record-cdp", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not (state["ready"] or errors) and time.monotonic() < deadline:
            time.sleep(0.1)
        if errors or not state["ready"]:
            reason = errors[0] if errors else "timed out attaching to the browser"
            print(f"Could not start the recorder: {reason}")
            print("Without CDP: hermes record --manual")
            return 1
        print(f"● Recording {state['url'] or 'the active tab'}")
        print("  Clicks, field values (passwords masked), Enter presses and navigations are")
        print("  being captured. Press Ctrl-C to stop and save.\n")
        while thread.is_alive():
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        thread.join(timeout=5)

    if not state["ready"]:
        return 1
    if errors and not events:
        print(f"\nThe recorder stopped with an error and captured nothing: {errors[0]}")
        return 1
    if not events:
        print("\nNo events captured; nothing saved.")
        return 1
    if errors:
        print(f"\nThe recorder stopped early ({errors[0]}); saving what was captured.")
    recording = build_recording(state["url"], started_at, events)
    path = save_recording(recording, args.slug or "recording")
    _print_next_step(path, len(recording["steps"]))
    return 0
