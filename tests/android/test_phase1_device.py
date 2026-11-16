"""Real-phone tests. Run with WINPHONE_TEST_ANDROID=<serial>. They only list, probe, screenshot and
open the viewer: no taps, no typing, no installs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from winphone_android.adapter import AndroidAdapter
from winphone_core.artifacts import ArtifactStore
from winphone_core.models import Capability, DeviceState
from winphone_core.orchestrator import Orchestrator
from winphone_core.tools import resolve

SERIAL = os.environ.get("WINPHONE_TEST_ANDROID", "")
pytestmark = [
    pytest.mark.android,
    pytest.mark.skipif(not SERIAL, reason="set WINPHONE_TEST_ANDROID=<serial> to run real-phone tests"),
]


@pytest.fixture(scope="module")
def orch(tmp_path_factory: pytest.TempPathFactory) -> Orchestrator:
    return Orchestrator([AndroidAdapter()], ArtifactStore(tmp_path_factory.mktemp("artifacts")))


def display_size() -> tuple[int, int]:
    adb = str(resolve("adb").path)
    out = subprocess.run([adb, "-s", SERIAL, "shell", "wm", "size"], capture_output=True, text=True,
                         timeout=15, check=True).stdout
    sizes = dict(re.findall(r"(Physical|Override) size: (\d+x\d+)", out))
    w, h = (sizes.get("Override") or sizes["Physical"]).split("x")
    return int(w), int(h)


def test_device_listed_online(orch: Orchestrator) -> None:
    d = orch.resolve(SERIAL)
    assert d.state is DeviceState.ONLINE
    assert d.model and d.sdk


def test_screenshot_matches_display(orch: Orchestrator) -> None:
    art = orch.screenshot(f"android:{SERIAL}")
    w, h = display_size()
    assert (art.width, art.height) in {(w, h), (h, w)}
    assert Path(art.path).stat().st_size > 1000


def test_capabilities_probed(orch: Orchestrator) -> None:
    caps = {c.capability: c for c in orch.capabilities(f"android:{SERIAL}")}
    assert caps[Capability.SCREENSHOT].supported
    assert "Phase 2" in (caps[Capability.OBSERVE_TREE].reason or "")


@pytest.fixture
def viewer(orch: Orchestrator) -> Iterator[int]:
    pid = orch.live_view(f"android:{SERIAL}")
    yield pid
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, check=False)


def winphone(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WINPHONE_HOME": str(home)}
    return subprocess.run([sys.executable, "-m", "winphone_cli", "--json", *args], capture_output=True,
                          text=True, timeout=120, env=env, check=False)


def test_cli_end_to_end(tmp_path: Path) -> None:
    """Auto-starts a private daemon (own WINPHONE_HOME, tools copied, no download) and stops it."""
    shutil.copytree(Path(os.environ["LOCALAPPDATA"]) / "winphone" / "tools", tmp_path / "tools")
    try:
        listed = winphone(tmp_path, "devices")
        assert listed.returncode == 0, listed.stderr
        assert SERIAL in {d["serial"] for d in json.loads(listed.stdout)}
        shot = winphone(tmp_path, "screenshot", "-d", SERIAL)
        assert shot.returncode == 0, shot.stdout + shot.stderr
        assert Path(json.loads(shot.stdout)["path"]).exists()
    finally:
        assert winphone(tmp_path, "daemon", "stop").returncode == 0


def test_live_view_starts_scrcpy(viewer: int) -> None:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {viewer}", "/NH"], capture_output=True, text=True,
                         timeout=15, check=True).stdout
    assert "scrcpy" in out.lower()
