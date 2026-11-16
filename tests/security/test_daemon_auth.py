from pathlib import Path

import pytest
from fakes import FakeAdapter, dev
from fastapi import FastAPI
from fastapi.testclient import TestClient
from winphone_core.artifacts import ArtifactStore
from winphone_core.orchestrator import Orchestrator
from winphone_core.server import create_app

T = "secret-token-value"
PORT = 4555


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shutdowns() -> list[int]:
    return []


@pytest.fixture
def app(tmp_path: Path, shutdowns: list[int]) -> FastAPI:
    orch = Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path))
    return create_app(orch, token=T, port=PORT, on_shutdown=lambda: shutdowns.append(1))


@pytest.fixture
def c(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def test_no_token_is_401(c: TestClient) -> None:
    assert c.get("/devices").status_code == 401


def test_wrong_token_is_401(c: TestClient) -> None:
    assert c.get("/devices", headers=auth("nope")).status_code == 401


def test_healthz_needs_token_too(c: TestClient) -> None:
    assert c.get("/healthz").status_code == 401
    assert c.get("/healthz", headers=auth(T)).json()["version"]


def test_foreign_origin_is_403(c: TestClient) -> None:
    r = c.get("/devices", headers={**auth(T), "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_tauri_origin_ok(c: TestClient) -> None:
    assert c.get("/devices", headers={**auth(T), "Origin": "tauri://localhost"}).status_code == 200


def test_bad_host_is_403(app: FastAPI) -> None:
    r = TestClient(app, base_url="http://evil.example").get("/devices", headers=auth(T))
    assert r.status_code == 403


def test_error_body_carries_code_and_no_token(c: TestClient) -> None:
    r = c.post("/devices/android:nope/screenshot", headers=auth(T))
    assert r.status_code == 404
    assert r.json()["error"] == "device_not_found"
    assert T not in r.text


def test_screenshot_route_returns_artifact(c: TestClient) -> None:
    r = c.post("/devices/android:A/screenshot", headers=auth(T))
    assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (4, 8)


def test_pair_rejects_option_like_host(c: TestClient) -> None:
    r = c.post("/wifi/pair", headers=auth(T), json={"host": "-x", "port": 5555, "code": "123456"})
    assert r.status_code == 422


class ExplodingAdapter(FakeAdapter):
    def screenshot(self, serial: str) -> bytes:
        raise RuntimeError("adb pair 10.0.0.5:1 123456 secret detail")


def test_unexpected_error_is_adapter_error_without_details(tmp_path: Path) -> None:
    """Review finding 2: a non-WinphoneError became HTTP 500 -> 'daemon unavailable' (exit 7)."""
    orch = Orchestrator([ExplodingAdapter([dev("A")])], ArtifactStore(tmp_path))
    app = create_app(orch, token=T, port=PORT, on_shutdown=lambda: None)
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False)
    r = c.post("/devices/android:A/screenshot", headers=auth(T))
    assert r.status_code == 500
    assert r.json()["error"] == "adapter_error"
    assert "RuntimeError" in r.json()["message"]
    assert "123456" not in r.text and "secret" not in r.text


def test_shutdown_calls_hook(c: TestClient, shutdowns: list[int]) -> None:
    assert c.post("/shutdown", headers=auth(T)).status_code == 200 and shutdowns == [1]
