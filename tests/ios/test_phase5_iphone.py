"""Phase 5 on a real iPhone, from Windows only. Run with WINPHONE_TEST_IOS=<udid>.

Read-only: lists, reads info, apps, logs and crash names. Nothing on the iPhone is changed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import CapabilityUnsupported
from winphone_core.models import Capability, DeviceState
from winphone_core.orchestrator import Orchestrator
from winphone_ios.adapter import WindowsIOSAdapter

UDID = os.environ.get("WINPHONE_TEST_IOS", "")
ID = f"ios:{UDID}"
ANDROID = os.environ.get("WINPHONE_TEST_ANDROID", "")
pytestmark = [
    pytest.mark.ios,
    pytest.mark.skipif(not UDID, reason="set WINPHONE_TEST_IOS=<udid> to run real-iPhone tests"),
]


@pytest.fixture(scope="module")
def orch(tmp_path_factory: pytest.TempPathFactory) -> Orchestrator:
    return Orchestrator([WindowsIOSAdapter()], ArtifactStore(tmp_path_factory.mktemp("artifacts")))


def test_iphone_listed_online(orch: Orchestrator) -> None:
    d = orch.resolve(UDID)
    assert (d.id, d.state) == (ID, DeviceState.ONLINE)
    assert d.model and d.os_version


def test_capabilities_are_honest(orch: Orchestrator) -> None:
    caps = {c.capability: c for c in orch.capabilities(ID)}
    assert {c for c, s in caps.items() if s.supported} == {Capability.LIST_APPS, Capability.LOGS,
                                                            Capability.CRASH_REPORTS}
    assert "developer disk image" in (caps[Capability.SCREENSHOT].reason or "")
    assert "WebDriverAgent" in (caps[Capability.TAP].reason or "")


def test_apps_include_settings(orch: Orchestrator) -> None:
    assert orch.apps(ID)  # the owner's own apps
    assert "com.apple.Preferences" in orch.apps(ID, include_system=True)


def test_logs_return_lines(orch: Orchestrator) -> None:
    assert orch.logs(ID, lines=5)


def test_crash_reports_list(orch: Orchestrator) -> None:
    assert isinstance(orch.crash_reports(ID), list)


def test_screenshot_is_refused_with_the_reason(orch: Orchestrator) -> None:
    with pytest.raises(CapabilityUnsupported) as e:
        orch.screenshot(ID)
    assert "developer disk image" in str(e.value)


@pytest.mark.skipif(not ANDROID, reason="needs the Android phone too (WINPHONE_TEST_ANDROID)")
def test_cli_lists_both_phones_and_requires_a_choice(tmp_path: Path) -> None:
    shutil.copytree(Path(os.environ["LOCALAPPDATA"]) / "winphone" / "tools", tmp_path / "tools")
    env = {**os.environ, "WINPHONE_HOME": str(tmp_path)}

    def winphone(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "winphone_cli", "--json", *args], capture_output=True,
                              text=True, timeout=180, env=env, check=False)

    try:
        listed = winphone("devices")
        assert listed.returncode == 0, listed.stderr
        ids = {d["id"] for d in json.loads(listed.stdout)}
        assert {ID, f"android:{ANDROID}"} <= ids
        both = winphone("screenshot")
        assert both.returncode == 2 and json.loads(both.stdout)["error"] == "device_ambiguous"
        refused = winphone("screenshot", "-d", UDID)
        assert refused.returncode == 4
    finally:
        assert winphone("daemon", "stop").returncode == 0
