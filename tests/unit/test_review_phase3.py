"""Regression tests for the Phase 3 review findings (each failed before its fix)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fakes import HOME, LOGIN, FakeAdapter, dev, ui_xml
from fastapi.testclient import TestClient
from winphone_core.artifacts import ArtifactStore
from winphone_core.audit import AuditLog
from winphone_core.errors import PermissionDenied
from winphone_core.orchestrator import Orchestrator
from winphone_core.permissions import (
    CLIENT_HEADER,
    ClientKind,
    PermissionManager,
    SensitivityPolicy,
    Tier,
    quoted,
)
from winphone_core.server import create_app

T, PORT, A = "tok", 4573, "android:A"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from winphone_core import actions

    monkeypatch.setattr(actions, "POLL_S", 0.01)
    monkeypatch.setattr(actions, "FOCUS_SETTLE_S", 0.0)


class Rig:
    def __init__(self, tmp_path: Path, screens: list[tuple[str, str | None]] | None = None,
                 on_confirm: Any = None) -> None:
        self.prompts: list[str] = []
        self.answers: list[bool] = []
        self.now = [1000.0]
        self.on_confirm = on_confirm
        self.fake = FakeAdapter([dev("A")], screens=screens)
        self.perms = PermissionManager(confirm=self.confirm, audit=AuditLog(tmp_path / "audit.jsonl"),
                                       policy=SensitivityPolicy.default(), clock=lambda: self.now[0])
        app = create_app(Orchestrator([self.fake], ArtifactStore(tmp_path / "art")), T, PORT, lambda: None,
                         self.perms)
        self.c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def confirm(self, message: str) -> bool:
        self.prompts.append(message)
        if self.on_confirm:
            self.on_confirm()
        return self.answers.pop(0) if self.answers else False

    def post(self, path: str, kind: str, body: dict[str, Any]) -> Any:
        return self.c.post(path, headers={"Authorization": f"Bearer {T}", CLIENT_HEADER: kind}, json=body)

    def taps(self) -> list[tuple[str, ...]]:
        return [c for c in self.fake.calls if c[0] == "tap"]


def denied(r: Any) -> bool:
    return r.status_code == 409 and r.json()["error"] == "permission_denied"


# 1 (critical): NUL stdin is not a console ----------------------------------------------------------

def test_nul_stdin_is_an_agent_not_a_human() -> None:
    code = "from winphone_cli.main import _kind; print(_kind().value)"
    out = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=120, check=True).stdout.strip()
    assert out == "agent"


# 2: the sensitive check sees every label under the tap point ----------------------------------------

def test_point_tap_on_send_is_sensitive(tmp_path: Path) -> None:
    send = ui_xml({"text": "Send", "class": "android.widget.Button", "clickable": "true", "bounds": "[0,0][200,100]"})
    rig = Rig(tmp_path, screens=[(send, "com.example"), (HOME, "com.example")])
    r = rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "tap", "target": {"point": [50, 50]}}})
    assert denied(r) and rig.taps() == [] and '"Send"' in rig.prompts[0]


def test_send_in_description_or_child_is_sensitive(tmp_path: Path) -> None:
    screen = ui_xml(
        {"class": "android.widget.FrameLayout", "clickable": "true", "resource-id": "com.x:id/row",
         "bounds": "[0,0][500,200]"},
        {"text": "Pay now", "bounds": "[10,10][200,90]"},
        {"text": "➤", "content-desc": "Send", "class": "android.widget.ImageButton", "clickable": "true",
         "bounds": "[300,300][400,400]"})
    rig = Rig(tmp_path, screens=[(screen, "com.example")])
    assert denied(rig.post(f"/devices/{A}/act", "human",
                           {"action": {"kind": "tap", "target": {"selector": {"resource_id": "row"}}}}))
    assert denied(rig.post(f"/devices/{A}/act", "human",
                           {"action": {"kind": "tap", "target": {"selector": {"desc": "Send"}}}}))
    assert rig.taps() == [] and len(rig.prompts) == 2


# 3: the screen changed while the window was open -> nothing is sent -----------------------------------

def test_screen_change_during_approval_sends_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, screens=[(LOGIN, "com.android.settings"), (HOME, "com.android.settings")])
    rig.on_confirm = lambda: setattr(rig.fake, "index", 1)  # the screen moves while the human reads
    rig.answers = [True]
    r = rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "tap", "target": {"selector": {"text": "Sign in"}}}})
    assert r.json()["error"] == "stale_reference" and rig.taps() == []


# 4: install is checked before anyone is asked; no network paths --------------------------------------

def test_install_bad_path_never_asks(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    for bad in (r"\\attacker\share\x.apk", "relative.apk", str(tmp_path / "missing.apk"), str(tmp_path / "notes.txt")):
        r = rig.post(f"/devices/{A}/install", "human", {"apk": bad})
        assert r.json()["error"] == "invalid_request", bad
    assert rig.prompts == []


def test_install_asks_with_full_path_and_installs_a_copy(tmp_path: Path) -> None:
    apk = tmp_path / "MyApp.apk"
    apk.write_bytes(b"PK" * 10)
    rig = Rig(tmp_path)
    rig.answers = [True]
    assert rig.post(f"/devices/{A}/install", "human", {"apk": str(apk)}).status_code == 200
    assert str(apk) in rig.prompts[0] and "20 bytes" in rig.prompts[0]
    assert rig.fake.calls == [("install", "MyApp.apk")]


# 5: the window does not steal the keyboard -----------------------------------------------------------

def test_window_does_not_take_keyboard_focus() -> None:
    from winphone_core.approval_window import WINDOW_FLAGS

    assert not WINDOW_FLAGS & 0x10000  # MB_SETFOREGROUND: a stray 'y' keypress would answer Yes
    assert WINDOW_FLAGS & 0x40000 and WINDOW_FLAGS & 0x100  # MB_TOPMOST, default button = No


# 6: one window at a time, and a cool-down after a Deny -------------------------------------------------

def test_one_window_at_a_time(tmp_path: Path) -> None:
    release = threading.Event()
    calls: list[str] = []

    def slow_confirm(message: str) -> bool:
        calls.append(message)
        release.wait(5)
        return False

    perms = PermissionManager(confirm=slow_confirm, audit=AuditLog(tmp_path / "a.jsonl"),
                              policy=SensitivityPolicy.default())
    t = threading.Thread(target=lambda: pytest.raises(PermissionDenied, perms.request_grant, ClientKind.AGENT, A, 5))
    t.start()
    time.sleep(0.2)
    with pytest.raises(PermissionDenied) as e:
        perms.request_grant(ClientKind.AGENT, "android:B", 5)
    assert "already waiting" in str(e.value) and len(calls) == 1
    release.set()
    t.join()


def test_cooldown_after_deny(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert denied(rig.post("/authorize", "agent", {"device": A, "minutes": 5}))
    assert denied(rig.post("/authorize", "agent", {"device": A, "minutes": 5}))
    assert len(rig.prompts) == 1  # the second ask never reached the human
    rig.now[0] += 11
    rig.post("/authorize", "agent", {"device": A, "minutes": 5})
    assert len(rig.prompts) == 2


# 7: MCP actions reconnect after the daemon restarts ------------------------------------------------------

def test_mcp_action_reconnects_after_daemon_restart() -> None:
    import asyncio

    from mcp import Client
    from winphone_core.errors import DaemonUnavailable
    from winphone_mcp.server import build_server

    made: list[int] = []

    class Dead:
        def resolve(self, device: str | None) -> Any:
            raise DaemonUnavailable("gone")

        def authorize(self, device: str | None, minutes: int) -> dict[str, Any]:
            raise DaemonUnavailable("gone")

    class Alive:
        def resolve(self, device: str | None) -> Any:
            return type("D", (), {"id": A})()

        def revoke(self, device: str | None) -> int:
            return 0

        def authorize(self, device: str | None, minutes: int) -> dict[str, Any]:
            return {"device": A, "expires_at": 0}

    def connect() -> Any:
        made.append(1)
        return Dead() if len(made) == 1 else Alive()

    async def go() -> list[bool]:
        async with Client(build_server(connect)) as c:
            first = await c.call_tool("phone_authorize", {})
            second = await c.call_tool("phone_authorize", {})
            return [first.is_error, second.is_error]

    assert asyncio.run(go()) == [True, False] and len(made) == 2


# 9, 11, 12: window text and policy ---------------------------------------------------------------------

def test_quoted_strips_control_and_invisible_characters() -> None:
    shown = quoted("Cancel\r\n\tAllow\u2028\u202eevil\u200b")
    assert all(ch.isprintable() for ch in shown)


def test_installer_and_permission_screens_are_sensitive() -> None:
    p = SensitivityPolicy.default()
    for pkg in ("com.android.packageinstaller", "com.google.android.packageinstaller",
                "com.android.permissioncontroller", "com.google.android.permissioncontroller"):
        assert p.tier_of("tap", pkg, "Install") is Tier.SENSITIVE


def test_type_prompt_never_shows_the_fields_text(tmp_path: Path) -> None:
    field = ui_xml({"text": "my old secret", "resource-id": "com.x:id/note", "class": "android.widget.EditText",
                    "clickable": "true", "bounds": "[0,0][500,100]"})
    rig = Rig(tmp_path, screens=[(field, "com.android.settings"), (HOME, "com.android.settings")])
    rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "type", "text": "x",
                                                       "target": {"selector": {"resource_id": "note"}}}})
    assert rig.prompts and "my old secret" not in rig.prompts[0] and "my old secret" not in (
        tmp_path / "audit.jsonl").read_text(encoding="utf-8")


# 16: typed text has a sane size limit --------------------------------------------------------------------

def test_typed_text_has_a_limit() -> None:
    from pydantic import ValidationError
    from winphone_core.actions import Action

    with pytest.raises(ValidationError):
        Action(kind="type", text="x" * 10_001)
