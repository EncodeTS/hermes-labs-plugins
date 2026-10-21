"""Live recorder against a real headless Chromium: real clicks and typing through CDP input
events on a local login page. Proves the in-page layer: the password never crosses the CDP
socket (the raw binding payloads already hold the placeholder), and the saved recording has
the clicks, values and navigations in order. Skipped when no Chromium-family browser is found."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

PASSWORD = "correct-horse-battery-staple-91"

# file:// pages: no HTTP server, so the test runs the same on every OS and on hosts where the
# browser's network stack is locked down.
LOGIN_PAGE = """<!doctype html><title>Login</title>
<form id="login" onsubmit="event.preventDefault(); location.href = 'home.html';">
  <input id="user" name="user" type="text">
  <input name="pw" type="password">
  <button id="go" type="submit">Sign in</button>
</form>"""
HOME_PAGE = """<!doctype html><title>Home</title>
<input name="q" type="search"><a id="orders" href="orders.html">Orders</a>"""
ORDERS_PAGE = "<!doctype html><title>Orders</title><p>No orders.</p>"


def _find_browser() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        if found := shutil.which(name):
            return found
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


BROWSER = _find_browser()
pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chromium-family browser on this host")


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    for name, body in (("login.html", LOGIN_PAGE), ("home.html", HOME_PAGE), ("orders.html", ORDERS_PAGE)):
        (root / name).write_text(body, encoding="utf-8")
    return root.as_uri()


@pytest.fixture
def browser(tmp_path, site):
    profile = tmp_path / "chrome-profile"
    args = [BROWSER, "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--disable-gpu", f"{site}/login.html"]
    if sys.platform.startswith("linux"):
        args.insert(1, "--no-sandbox")  # throwaway profile on a local page; CI runners lack userns
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (port_file.is_file() and len(port_file.read_text().split()) >= 2):
        time.sleep(0.1)
    if not port_file.is_file():
        proc.kill()
        pytest.skip("browser did not open a DevTools port")
    port, path = port_file.read_text().split()[:2]
    # Wait until the tab has committed the site URL (the startup tab is about:blank first).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        tabs = json.loads(opener.open(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
        if any(t.get("type") == "page" and t.get("url", "").startswith(site) for t in tabs):
            break
        time.sleep(0.1)
    yield f"ws://127.0.0.1:{port}{path}"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class Driver:
    """A second CDP client that acts like the user: mouse clicks and typed text."""

    def __init__(self, ws_url: str):
        from websockets.sync.client import connect

        from workflow_record_plugin.cdp import pick_page_target

        self.ws = connect(ws_url, max_size=None, proxy=None)
        self.next_id = 0
        page = pick_page_target(self.call("Target.getTargets")["targetInfos"])  # same tab as the recorder
        self.sid = self.call("Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})["sessionId"]
        self.call("Runtime.enable", session=True)

    def call(self, method, params=None, session=False):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = self.sid
        self.ws.send(json.dumps(msg))
        while True:
            reply = json.loads(self.ws.recv(timeout=15))
            if reply.get("id") == self.next_id:
                assert "error" not in reply, reply
                return reply.get("result", {})

    def eval(self, expression):
        return self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                         session=True)["result"].get("value")

    def wait_for(self, expression, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.eval(expression):
                    return
            except AssertionError:
                pass  # context torn down mid-navigation
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for {expression}")

    def click(self, selector):
        self.wait_for(f"!!document.querySelector({json.dumps(selector)})")
        box = self.eval(f"(() => {{ const r = document.querySelector({json.dumps(selector)})"
                        ".getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": kind, "x": box[0], "y": box[1],
                                                   "button": "left", "clickCount": 1}, session=True)

    def type(self, text):
        self.call("Input.insertText", {"text": text}, session=True)

    def key(self, key, code, vk):
        for kind in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": code,
                                                 "windowsVirtualKeyCode": vk}, session=True)

    def close(self):
        self.ws.close()


def test_live_recording_masks_password_in_the_page(browser, site):
    from workflow_record_plugin.cdp import record_over_cdp
    from workflow_record_plugin.recording import build_recording

    events, state, stop, errors = [], {"url": "", "ready": False}, threading.Event(), []

    def run():
        try:
            asyncio.run(record_over_cdp(browser, events, stop, state))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not (state["ready"] or errors) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert state["ready"], errors

    driver = Driver(browser)
    try:
        driver.wait_for("!!window.__hermesRecorderInstalled")
        driver.click("#user")
        driver.type("alice")
        driver.click("input[name=pw]")  # blurs #user -> change
        driver.type(PASSWORD)
        driver.click("#go")             # blurs the password field -> change, then submits
        driver.wait_for("document.title === 'Home' && !!window.__hermesRecorderInstalled")
        driver.click("input[name=q]")
        driver.type("blue widgets")
        driver.key("Enter", "Enter", 13)
        driver.click("#orders")
        driver.wait_for("document.title === 'Orders'")
        time.sleep(0.5)
    finally:
        driver.close()
        stop.set()
        thread.join(timeout=10)
    assert not errors, errors

    # Layer 1: the raw payloads that crossed the CDP socket never held the password.
    assert PASSWORD not in json.dumps(events)

    rec = build_recording(state["url"], "now", events)
    steps = rec["steps"]
    assert rec["url"] == f"{site}/login.html"
    inputs = [(s["selector"].rsplit(" > ", 1)[-1], s["value"]) for s in steps if s["type"] == "input"]
    assert inputs == [("input#user", "alice"), ('input[name="pw"]', "{SECRET:pw}"),
                      ('input[name="q"]', "blue widgets")]
    assert any(s["type"] == "click" and s.get("text") == "Sign in" for s in steps)
    assert any(s["type"] == "enter" and s["selector"].endswith('input[name="q"]') for s in steps)
    navigations = [s["url"] for s in steps if s["type"] == "navigate"]
    assert navigations == [f"{site}/home.html", f"{site}/orders.html"]
    assert [s["t"] for s in steps] == sorted(s["t"] for s in steps)
    assert PASSWORD not in json.dumps(rec)
