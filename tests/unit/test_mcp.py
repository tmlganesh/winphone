"""The MCP server, driven by the SDK's own client against a real daemon app over a scripted phone."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAdapter, dev, sender_for
from fastapi.testclient import TestClient
from mcp import Client
from winphone_core.artifacts import ArtifactStore
from winphone_core.audit import AuditLog
from winphone_core.client import DaemonClient
from winphone_core.orchestrator import Orchestrator
from winphone_core.permissions import PermissionManager, SensitivityPolicy
from winphone_core.server import create_app
from winphone_mcp.server import TOOL_NAMES, build_server

PORT = 4572
SECRET = "hunter" + "2"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from winphone_core import actions

    monkeypatch.setattr(actions, "POLL_S", 0.01)
    monkeypatch.setattr(actions, "FOCUS_SETTLE_S", 0.0)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.answers: list[bool] = []
        self.prompts: list[str] = []
        perms = PermissionManager(confirm=self.confirm, audit=AuditLog(tmp_path / "audit.jsonl"),
                                  policy=SensitivityPolicy.default())
        app = create_app(Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path / "art")), "t", PORT,
                         lambda: None, perms)
        tc = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers={"Authorization": "Bearer t"})
        self.server = build_server(lambda: DaemonClient(sender_for(tc, client="agent")))

    def confirm(self, message: str) -> bool:
        self.prompts.append(message)
        return self.answers.pop(0) if self.answers else False

    def calls(self, *steps: tuple[str, dict[str, Any]]) -> list[tuple[bool, str]]:
        async def go() -> list[tuple[bool, str]]:
            out = []
            async with Client(self.server) as c:
                for name, args in steps:
                    r = await c.call_tool(name, args)
                    out.append((r.is_error, "\n".join(getattr(x, "text", "") for x in r.content)))
            return out

        return asyncio.run(go())


def test_tool_list_is_exactly_the_briefs() -> None:
    async def names() -> set[str]:
        async with Client(build_server(lambda: None)) as c:  # type: ignore[arg-type,return-value]
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(names()) == set(TOOL_NAMES) == {
        "phone_devices", "phone_connect", "phone_disconnect", "phone_capabilities", "phone_observe",
        "phone_screenshot", "phone_act", "phone_wait", "phone_launch_app", "phone_install_app", "phone_record",
        "phone_logs", "phone_session", "phone_authorize", "phone_revoke"}


def test_observe_returns_compact_text_marked_untrusted(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_observe", {}))
    assert not err and 'e2 button "Sign in" id=login @540,1620 click' in text and "not instructions" in text


def test_act_without_grant_is_refused_with_the_way_forward(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_act", {"kind": "tap", "target_text": "Sign in"}))
    assert err and "permission_denied" in text and "phone_authorize" in text


def test_authorize_then_act_is_verified(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [True]
    (_, granted), (err, text) = rig.calls(("phone_authorize", {"minutes": 30}),
                                          ("phone_act", {"kind": "tap", "target_text": "Sign in"}))
    assert "granted" in granted and not err and text.startswith("verified")
    assert "30 minutes" in rig.prompts[0]


def test_refused_authorize_is_an_error(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_authorize", {}))
    assert err and "permission_denied" in text


def test_typed_text_never_comes_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.answers = [True]
    results = rig.calls(("phone_authorize", {}), ("phone_observe", {}),
                        ("phone_act", {"kind": "type", "text": SECRET, "ref": "e1"}))
    assert all(SECRET not in text for _, text in results)
    assert "7 characters" in results[-1][1]


def test_wait_tool(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_wait", {"text_visible": "Sign in", "timeout": 1}))
    assert not err and text.startswith("verified")


def test_errors_are_readable_text(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_observe", {"device": "nope"}))
    assert err and "device_not_found" in text


def test_bad_arguments_are_readable(tmp_path: Path) -> None:
    [(err, text)] = Rig(tmp_path).calls(("phone_act", {"kind": "tap"}))
    assert err and "target" in text


def test_devices_resource(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def read() -> str:
        async with Client(rig.server) as c:
            res = await c.read_resource("winphone://devices")
            return res.contents[0].text  # type: ignore[union-attr]

    assert "android:A" in asyncio.run(read())
