import json
from pathlib import Path
from typing import Any

import pytest
from fakes import LOGIN, FakeAdapter, dev, sender_for
from fastapi.testclient import TestClient
from typer.testing import CliRunner, Result
from winphone_cli import main
from winphone_core.artifacts import ArtifactStore
from winphone_core.client import DaemonClient
from winphone_core.models import Capability, DeviceState
from winphone_core.orchestrator import Orchestrator
from winphone_core.server import create_app

PORT = 4556


def use_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeAdapter) -> None:
    app = create_app(Orchestrator([fake], ArtifactStore(tmp_path / "art")), token="t", port=PORT,
                     on_shutdown=lambda: None)
    http = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers={"Authorization": "Bearer t"})

    def fake_connect(**kwargs: Any) -> DaemonClient:
        return DaemonClient(sender_for(http))

    monkeypatch.setattr(main, "connect_daemon", fake_connect)


def run(*args: str) -> Result:
    return CliRunner().invoke(main.app, list(args))


def test_devices_json_lists_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("--json", "devices")
    assert r.exit_code == 0
    assert json.loads(r.stdout)[0]["id"] == "android:A"


def test_devices_human_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("devices")
    assert r.exit_code == 0 and "android:A" in r.stdout and "online" in r.stdout


def test_devices_shows_hint_for_unauthorized_phone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A", DeviceState.UNAUTHORIZED)]))
    r = run("devices")
    assert r.exit_code == 0 and "Allow USB debugging" in r.stdout


def test_ambiguous_device_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A"), dev("B")]))
    r = run("screenshot")
    assert r.exit_code == 2
    assert "--device" in r.stderr


def test_unsupported_capability_exit_4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")], unsupported={Capability.LIVE_VIEW: "no scrcpy"}))
    r = run("view", "-d", "A")
    assert r.exit_code == 4 and "no scrcpy" in r.stderr


def test_screenshot_copies_to_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeAdapter([dev("A")])
    use_fake(monkeypatch, tmp_path, fake)
    out = tmp_path / "shot.png"
    r = run("screenshot", "-o", str(out))
    assert r.exit_code == 0 and out.read_bytes() == fake.png


def test_error_json_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("--json", "screenshot", "-d", "Z")
    assert r.exit_code == 3
    assert json.loads(r.stdout)["error"] == "device_not_found"


def test_connect_wifi_bad_address_is_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter())
    assert run("connect", "--wifi", "10.0.0.5").exit_code == 2


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from winphone_core import actions

    monkeypatch.setattr(actions, "POLL_S", 0.01)
    monkeypatch.setattr(actions, "FOCUS_SETTLE_S", 0.0)


def test_observe_prints_compact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("observe")
    assert r.exit_code == 0 and 'e2 button "Sign in" id=login @540,1620 click' in r.stdout


def test_observe_find_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("observe", "--find", "sign")
    assert r.exit_code == 0 and "Sign in" in r.stdout and "password" not in r.stdout


def test_tap_by_text_exit_0_when_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast: None) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("tap", "--text", "Sign in")
    assert r.exit_code == 0 and r.stdout.startswith("verified")


def test_tap_not_verified_exit_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast: None) -> None:
    fake = FakeAdapter([dev("A")])
    fake.advance_on_input = False
    use_fake(monkeypatch, tmp_path, fake)
    r = run("tap", "--text", "Sign in", "--wait-timeout", "0.05")
    assert r.exit_code == 1 and r.stdout.startswith("not_verified")


def test_tap_by_ref_after_observe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast: None) -> None:
    fake = FakeAdapter([dev("A")])
    use_fake(monkeypatch, tmp_path, fake)
    assert run("observe").exit_code == 0
    assert run("--json", "tap", "e2").exit_code == 0 and ("tap", "540", "1620") in fake.calls


def test_type_text_not_printed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast: None) -> None:
    secret = "hunter" + "2"
    for i, args in enumerate((("type", secret, "--into", "e1"), ("--json", "type", secret, "--into", "e1"))):
        use_fake(monkeypatch, tmp_path / str(i), FakeAdapter([dev("A")]))  # a fresh login screen each time
        run("observe")
        r = run(*args)
        assert r.exit_code == 0 and secret not in r.stdout + r.stderr


def test_type_reads_text_from_stdin_when_omitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                fast: None) -> None:
    """Review: a password on the command line ends up in PowerShell's history file."""
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    run("observe")
    secret = "hunter" + "2"
    r = CliRunner().invoke(main.app, ["type", "--into", "e1"], input=secret + "\n")
    assert r.exit_code == 0 and "7 characters" in r.stdout and secret not in r.stdout + r.stderr


def test_launch_wait_timeout_still_waits_for_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     fast: None) -> None:
    """Review: --wait-timeout alone turned 'app in front' into 'anything changed'."""
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")], screens=[(LOGIN, "com.launcher"),
                                                                      (LOGIN, "com.other")]))
    assert run("app", "launch", "com.example", "--wait-timeout", "0.05").exit_code == 1


def test_two_targets_is_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    assert run("tap", "e2", "--text", "Sign in").exit_code == 2


def test_press_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    assert run("press", "sideways").exit_code == 2


def test_app_launch_bad_package_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    assert run("app", "launch", "not a package").exit_code == 2


def test_apps_lists_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter([dev("A")]))
    r = run("apps")
    assert r.exit_code == 0 and "com.example" in r.stdout


def test_connect_wifi_holds_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter())
    r = run("--json", "connect", "--wifi", "10.0.0.5:5555")
    assert r.exit_code == 0
    d = json.loads(r.stdout)
    assert d["id"] == "android:10.0.0.5:5555" and d["held"] is True
