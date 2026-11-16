"""Permissions in the daemon: agents observe freely, interact only with a human's grant, and every
sensitive action needs a human click. Typed text never reaches the audit log or a prompt."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fakes import HOME, LOGIN, FakeAdapter, dev, ui_xml
from fastapi.testclient import TestClient
from winphone_core.artifacts import ArtifactStore
from winphone_core.audit import AuditLog
from winphone_core.orchestrator import Orchestrator
from winphone_core.permissions import CLIENT_HEADER, PermissionManager, SensitivityPolicy, Tier
from winphone_core.server import create_app

T, PORT, A = "tok", 4570, "android:A"
SIGN_IN = {"action": {"kind": "tap", "target": {"selector": {"text": "Sign in"}}}}


def h(kind: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {T}", **({CLIENT_HEADER: kind} if kind else {})}


class Rig:
    def __init__(self, tmp_path: Path, screens: list[tuple[str, str | None]] | None = None) -> None:
        self.answers: list[bool] = []
        self.prompts: list[str] = []
        self.now = [1000.0]
        self.audit_path = tmp_path / "audit.jsonl"
        self.fake = FakeAdapter([dev("A")], screens=screens)
        perms = PermissionManager(confirm=self.confirm, audit=AuditLog(self.audit_path),
                                  policy=SensitivityPolicy.default(), clock=lambda: self.now[0])
        app = create_app(Orchestrator([self.fake], ArtifactStore(tmp_path / "art")), T, PORT, lambda: None, perms)
        self.c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def confirm(self, message: str) -> bool:
        self.prompts.append(message)
        return self.answers.pop(0) if self.answers else False  # nobody answered: deny

    def post(self, path: str, kind: str | None, body: dict[str, Any] | None = None) -> Any:
        return self.c.post(path, headers=h(kind), json=body or {})

    def taps(self) -> list[tuple[str, ...]]:
        return [c for c in self.fake.calls if c[0] == "tap"]


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from winphone_core import actions

    monkeypatch.setattr(actions, "POLL_S", 0.01)
    monkeypatch.setattr(actions, "FOCUS_SETTLE_S", 0.0)


def denied(r: Any) -> bool:
    return r.status_code == 409 and r.json()["error"] == "permission_denied"


def test_agent_without_grant_cannot_tap(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    r = rig.post(f"/devices/{A}/act", "agent", SIGN_IN)
    assert denied(r) and "authorize" in (r.json()["hint"] or "")
    assert rig.taps() == [] and rig.prompts == []  # no window pops up unasked


def test_missing_client_header_counts_as_agent(tmp_path: Path) -> None:
    assert denied(Rig(tmp_path).post(f"/devices/{A}/act", None, SIGN_IN))


def test_agent_can_observe(tmp_path: Path) -> None:
    assert Rig(tmp_path).post(f"/devices/{A}/observe", "agent").status_code == 200


def test_grant_via_window_then_tap(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [True]
    r = rig.post("/authorize", "agent", {"device": A, "minutes": 30})
    assert r.status_code == 200 and A in rig.prompts[0] and "30 minutes" in rig.prompts[0]
    assert rig.post(f"/devices/{A}/act", "agent", SIGN_IN).json()["outcome"] == "verified"


def test_refused_or_unanswered_grant_denies(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [False]
    assert denied(rig.post("/authorize", "agent", {"device": A, "minutes": 30}))
    assert denied(rig.post("/authorize", "agent", {"device": A, "minutes": 30}))  # unanswered -> no
    assert denied(rig.post(f"/devices/{A}/act", "agent", SIGN_IN)) and rig.taps() == []


def test_grant_expires(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [True]
    rig.post("/authorize", "agent", {"device": A, "minutes": 1})
    rig.now[0] += 61
    assert denied(rig.post(f"/devices/{A}/act", "agent", SIGN_IN))


def test_revoke_drops_the_grant(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [True]
    rig.post("/authorize", "agent", {"device": A, "minutes": 30})
    assert rig.post("/revoke", "agent", {"device": A}).json()["revoked"] == 1
    assert denied(rig.post(f"/devices/{A}/act", "agent", SIGN_IN))


def test_human_taps_without_a_grant(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.post(f"/devices/{A}/act", "human", SIGN_IN).json()["outcome"] == "verified"
    assert rig.prompts == []


def test_sensitive_app_needs_a_click_every_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path, screens=[(LOGIN, "com.android.settings"), (HOME, "com.android.settings")])
    rig.answers = [False]
    assert denied(rig.post(f"/devices/{A}/act", "human", SIGN_IN)) and rig.taps() == []
    rig.answers = [True]
    assert rig.post(f"/devices/{A}/act", "human", SIGN_IN).status_code == 200
    assert len(rig.prompts) == 2 and "com.android.settings" in rig.prompts[0]


def test_agent_sensitive_needs_a_click_even_with_a_grant(tmp_path: Path) -> None:
    send = ui_xml({"text": "Send", "class": "android.widget.Button", "clickable": "true", "bounds": "[0,0][200,100]"})
    rig = Rig(tmp_path, screens=[(send, "com.example"), (HOME, "com.example")])
    rig.answers = [True, False]  # grant yes, then 'Send' no
    rig.post("/authorize", "agent", {"device": A, "minutes": 30})
    r = rig.post(f"/devices/{A}/act", "agent", {"action": {"kind": "tap", "target": {"selector": {"text": "Send"}}}})
    assert denied(r) and rig.taps() == [] and '"Send"' in rig.prompts[1]


def test_prompt_shows_untrusted_label_short_and_quoted(tmp_path: Path) -> None:
    label = "Send " + "x" * 200
    screen = ui_xml({"text": label, "class": "android.widget.Button", "clickable": "true", "bounds": "[0,0][200,100]"})
    rig = Rig(tmp_path, screens=[(screen, "com.example")])
    rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "tap", "target": {"point": [10, 10]}}})
    rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "tap", "target": {"selector": {"text": label}}}})
    assert rig.prompts and "x" * 60 not in rig.prompts[-1]


def test_audit_records_decisions_without_typed_text(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    secret = "hunter" + "2"
    rig.post(f"/devices/{A}/act", "agent", SIGN_IN)  # denied first: the login screen is still showing
    rig.post(f"/devices/{A}/act", "human", {"action": {"kind": "type", "text": secret,
                                                       "target": {"selector": {"resource_id": "password"}}}})
    text = rig.audit_path.read_text()
    assert secret not in text and '"decision": "allow"' in text and '"decision": "deny"' in text
    assert '"chars": 7' in text


def test_agent_cannot_pair_wifi(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert denied(rig.post("/wifi/pair", "agent", {"host": "10.0.0.5", "port": 37123, "code": "123456"}))


def test_policy_tiers() -> None:
    p = SensitivityPolicy.default()
    check: Callable[..., Tier] = p.tier_of
    assert check("install", "com.example", None) is Tier.SENSITIVE
    assert check("tap", "com.android.settings", "Display") is Tier.SENSITIVE
    assert check("tap", "com.example", "Pay now") is Tier.SENSITIVE
    assert check("tap", "com.example", "Sender name") is Tier.INTERACT  # whole words only
    assert check("tap", "com.example", "Display") is Tier.INTERACT
    assert check("launch", "com.example", None) is Tier.INTERACT
