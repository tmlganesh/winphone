import json
import socket
import time
from pathlib import Path

import pytest
from winphone_core.client import DaemonClient, connect_daemon, http_sender
from winphone_core.errors import CapabilityUnsupported, DaemonUnavailable
from winphone_core.runtime import DaemonInfo, write_info


def test_stale_daemon_file_respawns(tmp_home: Path) -> None:
    write_info(DaemonInfo(port=1, token="dead", pid=999999, version="0"))  # nothing listens on :1
    calls: list[int] = []
    with pytest.raises(DaemonUnavailable):
        connect_daemon(spawn=lambda: calls.append(1), timeout=0.5)
    assert calls == [1]


def test_no_autostart_raises_without_spawning(tmp_home: Path) -> None:
    calls: list[int] = []
    with pytest.raises(DaemonUnavailable):
        connect_daemon(autostart=False, spawn=lambda: calls.append(1), timeout=0.5)
    assert calls == []


def test_error_body_becomes_typed_exception() -> None:
    body = json.dumps({"error": "capability_unsupported", "message": "no", "hint": None}).encode()
    client = DaemonClient(lambda method, path, payload: (409, body))
    with pytest.raises(CapabilityUnsupported):
        client.view("android:A")


def test_non_json_error_is_daemon_unavailable() -> None:
    client = DaemonClient(lambda method, path, payload: (500, b"Internal Server Error"))
    with pytest.raises(DaemonUnavailable):
        client.devices()


def test_observe_act_apps_round_trip(tmp_path: Path) -> None:
    from fakes import FakeAdapter, dev, sender_for
    from fastapi.testclient import TestClient
    from winphone_core.actions import Action, Target, Wait
    from winphone_core.artifacts import ArtifactStore
    from winphone_core.observation import Selector
    from winphone_core.orchestrator import Orchestrator
    from winphone_core.server import create_app

    app = create_app(Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path)), "t", 4557, lambda: None)
    client = DaemonClient(sender_for(TestClient(app, base_url="http://127.0.0.1:4557",
                                                headers={"Authorization": "Bearer t"})))
    obs = client.observe("android:A")
    assert obs.elements[-1].text == "Sign in"
    r = client.act("android:A", Action(kind="tap", target=Target(selector=Selector(text="Sign in"))),
                   wait=Wait(text_visible="Welcome", timeout_s=2), idempotency_key="k")
    assert r.outcome == "verified" and r.resolved_by == "selector"
    assert client.apps("android:A") == ["com.example"]


def test_dropped_connection_is_daemon_unavailable() -> None:
    def send(method: str, path: str, payload: bytes | None) -> tuple[int, bytes]:
        raise ConnectionResetError("gone")

    with pytest.raises(DaemonUnavailable):
        DaemonClient(send).devices()


def test_http_sender_talks_to_a_real_socket(tmp_path: Path) -> None:
    """The stdlib sender must send Host 127.0.0.1:<port>, the token and JSON, as the daemon requires."""
    import threading

    import uvicorn
    from fakes import FakeAdapter, dev
    from winphone_core.artifacts import ArtifactStore
    from winphone_core.orchestrator import Orchestrator
    from winphone_core.server import create_app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    orch = Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path))
    app = create_app(orch, "tok", port, lambda: None)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
    try:
        client = DaemonClient(http_sender(port, "tok", timeout=5))
        for _ in range(50):
            try:
                client.healthz()
                break
            except DaemonUnavailable:
                time.sleep(0.1)
        assert client.resolve("A").id == "android:A"
        with pytest.raises(DaemonUnavailable):  # a rejected token means a stale or foreign daemon
            DaemonClient(http_sender(port, "wrong", timeout=5)).devices()
    finally:
        server.should_exit = True
