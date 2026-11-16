"""Phase 2 on a real phone. Run with WINPHONE_TEST_ANDROID=<serial>.

Only the Settings app is used: open it, open pages, scroll, search, go back. Nothing is toggled,
sent or changed, and every test ends with the phone on the Home screen."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from winphone_android.adapter import AndroidAdapter
from winphone_core.actions import Action, Target, Wait
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import StaleReference
from winphone_core.models import Key
from winphone_core.observation import Selector, find, to_compact
from winphone_core.orchestrator import Orchestrator
from winphone_core.tools import resolve

SERIAL = os.environ.get("WINPHONE_TEST_ANDROID", "")
ID = f"android:{SERIAL}"
SETTINGS = "com.android.settings"
pytestmark = [
    pytest.mark.android,
    pytest.mark.skipif(not SERIAL, reason="set WINPHONE_TEST_ANDROID=<serial> to run real-phone tests"),
]


@pytest.fixture(scope="module")
def orch(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Orchestrator]:
    o = Orchestrator([AndroidAdapter()], ArtifactStore(tmp_path_factory.mktemp("artifacts")))
    yield o
    o.act(ID, Action(kind="press", key=Key.HOME))
    o.close()


@pytest.fixture
def settings(orch: Orchestrator) -> Iterator[Orchestrator]:
    # Launching resumes whatever page Settings was left on (as tapping its icon would): start clean.
    adb("shell", "am", "force-stop", SETTINGS)
    adb("shell", "am", "force-stop", "com.android.settings.intelligence")
    r = orch.act(ID, Action(kind="launch", package=SETTINGS))
    assert r.outcome == "verified", r.evidence
    yield orch
    orch.act(ID, Action(kind="press", key=Key.HOME))


def adb(*args: str) -> str:
    return subprocess.run([str(resolve("adb").path), "-s", SERIAL, *args], capture_output=True, text=True,
                          timeout=60, check=True).stdout


def test_launch_settings_verified(settings: Orchestrator) -> None:
    obs = settings.observe(ID)
    assert obs.foreground_app == SETTINGS and obs.elements


def test_observation_is_compact(settings: Orchestrator) -> None:
    compact = to_compact(settings.observe(ID))
    assert len(compact) < 6000, len(compact)


def scroll_to(orch: Orchestrator, text: str) -> None:
    for _ in range(6):
        if find(orch.observe(ID), Selector(text=text)):
            return
        orch.act(ID, Action(kind="swipe", direction="up"))
    pytest.fail(f"{text!r} not found after 6 swipes")


def test_tap_about_phone_and_back(settings: Orchestrator) -> None:
    scroll_to(settings, "About phone")
    r = settings.act(ID, Action(kind="tap", target=Target(selector=Selector(text="About phone"))))
    assert r.outcome == "verified", r.evidence
    back = settings.act(ID, Action(kind="press", key=Key.BACK))
    assert back.outcome == "verified", back.evidence


def test_stale_ref_is_never_a_blind_tap(settings: Orchestrator) -> None:
    scroll_to(settings, "About phone")
    obs = settings.observe(ID)
    about = find(obs, Selector(text="About phone"))
    settings.act(ID, Action(kind="tap", target=Target(ref=about[0].ref)))  # the screen changes
    try:
        r = settings.act(ID, Action(kind="tap", target=Target(ref=about[0].ref, observation_id=obs.observation_id)))
        assert r.resolved_by == "reresolved"  # found again by id+text+class, never the old coordinates
    except StaleReference:
        pass


def test_swipe_scrolls_the_apps_list(orch: Orchestrator) -> None:
    adb("shell", "am", "start", "-W", "-a", "android.settings.MANAGE_APPLICATIONS_SETTINGS")
    time.sleep(1.5)
    try:
        r = orch.act(ID, Action(kind="swipe", direction="up"))
        assert r.outcome == "verified", r.evidence
    finally:
        orch.act(ID, Action(kind="press", key=Key.HOME))


def test_search_and_type(settings: Orchestrator) -> None:
    r = settings.act(ID, Action(kind="tap", target=Target(selector=Selector(resource_id="search_action_bar"))))
    assert r.outcome == "verified", r.evidence
    typed = settings.act(ID, Action(kind="type", text="battery"), wait=Wait(text_visible="Battery", timeout_s=10))
    assert typed.outcome == "verified", typed.evidence
    assert "battery" not in typed.model_dump_json().replace("Battery", "")


def test_home_leaves_settings(settings: Orchestrator) -> None:
    settings.act(ID, Action(kind="press", key=Key.HOME))
    assert settings.observe(ID).foreground_app != SETTINGS


def test_cli_observe_through_daemon(tmp_path: Path) -> None:
    """A private daemon (own WINPHONE_HOME, tools copied) reads the screen through uiautomator2."""
    shutil.copytree(Path(os.environ["LOCALAPPDATA"]) / "winphone" / "tools", tmp_path / "tools")
    env = {**os.environ, "WINPHONE_HOME": str(tmp_path)}

    def winphone(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "winphone_cli", "--json", *args], capture_output=True,
                              text=True, timeout=180, env=env, check=False)

    try:
        r = winphone("observe", "-d", SERIAL)
        assert r.returncode == 0, r.stdout + r.stderr
        assert json.loads(r.stdout)["elements"]
    finally:
        assert winphone("daemon", "stop").returncode == 0
