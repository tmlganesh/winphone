"""Milestone 1 on a real phone, through MCP as an AI agent would do it. Run with WINPHONE_TEST_ANDROID=<serial>.

Needs the owner at the laptop: the grant window appears once and must be answered with Yes.
Uses the Calculator app (not sensitive, so no per-tap window): observe, tap '7' by ref, verify, clear.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from winphone_core.client import connect_daemon
from winphone_core.permissions import ClientKind
from winphone_mcp.server import build_server

SERIAL = os.environ.get("WINPHONE_TEST_ANDROID", "")
ID = f"android:{SERIAL}"
CALCULATORS = ("com.google.android.calculator", "com.nothing.calculator", "com.android.calculator2",
               "com.sec.android.app.popupcalculator", "com.miui.calculator", "com.oneplus.calculator")
pytestmark = [
    pytest.mark.android,
    pytest.mark.skipif(not SERIAL, reason="set WINPHONE_TEST_ANDROID=<serial> to run real-phone tests"),
]


@pytest.fixture(scope="module")
def private_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A private daemon (own WINPHONE_HOME, tools copied): grants and audit stay out of the owner's real one."""
    home = tmp_path_factory.mktemp("home")
    shutil.copytree(Path(os.environ["LOCALAPPDATA"]) / "winphone" / "tools", home / "tools")
    old = os.environ.get("WINPHONE_HOME")
    os.environ["WINPHONE_HOME"] = str(home)
    yield home
    try:
        connect_daemon(autostart=False, timeout=2).shutdown()
    finally:
        if old is None:
            os.environ.pop("WINPHONE_HOME", None)
        else:
            os.environ["WINPHONE_HOME"] = old


def run_steps(steps: list[tuple[str, dict[str, Any]]]) -> list[tuple[bool, str]]:
    server = build_server(lambda: connect_daemon(client_kind=ClientKind.AGENT, call_timeout=180))

    async def go() -> list[tuple[bool, str]]:
        out = []
        async with Client(server) as c:
            for name, args in steps:
                r = await c.call_tool(name, args)
                out.append((r.is_error, "\n".join(getattr(x, "text", "") for x in r.content)))
        return out

    return asyncio.run(go())


def test_milestone_1_through_mcp(private_home: Path) -> None:
    [(_, devices), (_, apps)] = run_steps([("phone_devices", {}), ("phone_logs", {"device": ID, "lines": 1})])
    assert ID in devices
    from winphone_core.client import connect_daemon as cd

    installed = set(cd(client_kind=ClientKind.AGENT).apps(ID, include_system=True))
    calculator = next((p for p in CALCULATORS if p in installed), None)
    assert calculator, "no known Calculator app on this phone"

    refused, granted, launched = run_steps([
        ("phone_launch_app", {"device": ID, "package": calculator}),  # no grant yet
        ("phone_authorize", {"device": ID, "minutes": 10}),  # the owner clicks Yes
        ("phone_launch_app", {"device": ID, "package": calculator}),
    ])
    assert refused[0] and "permission_denied" in refused[1]
    assert not granted[0], granted[1]
    assert not launched[0] and launched[1].startswith("verified"), launched[1]

    [(_, screen)] = run_steps([("phone_observe", {"device": ID})])
    seven = re.search(r'^(e\d+) \w+ "7"', screen, re.MULTILINE) or re.search(r'^(e\d+) .*desc="7"', screen, re.M)
    assert seven, screen[:2000]
    [(err, tapped)] = run_steps([("phone_act", {"device": ID, "kind": "tap", "ref": seven.group(1),
                                                "wait_text": "7", "wait_timeout": 5})])
    assert not err and tapped.startswith("verified"), tapped
    clear = run_steps([("phone_observe", {"device": ID, "find_text": "clear"})])[0][1]
    ref = re.search(r"^(e\d+) ", clear.split("\n", 2)[-1], re.MULTILINE)
    steps: list[tuple[str, dict[str, Any]]] = []
    if ref:
        steps.append(("phone_act", {"device": ID, "kind": "tap", "ref": ref.group(1)}))
    steps += [("phone_act", {"device": ID, "kind": "press", "key": "home"}), ("phone_revoke", {"device": ID})]
    run_steps(steps)
