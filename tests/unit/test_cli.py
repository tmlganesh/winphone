import json
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeAdapter, dev, sender_for
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


def test_connect_wifi_holds_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_fake(monkeypatch, tmp_path, FakeAdapter())
    r = run("--json", "connect", "--wifi", "10.0.0.5:5555")
    assert r.exit_code == 0
    d = json.loads(r.stdout)
    assert d["id"] == "android:10.0.0.5:5555" and d["held"] is True
