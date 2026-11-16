"""Client for winphoned, shared by the CLI and (Phase 3) the MCP server. Starts the daemon if needed.

Uses the standard library's http.client: the daemon is plain HTTP on 127.0.0.1, and httpx cost
~2.4 s of import and SSL setup on every CLI call."""

from __future__ import annotations

import http.client
import json
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from winphone_core.actions import Action, Receipt, Wait
from winphone_core.errors import DaemonUnavailable, InvalidRequest, error_from_dict
from winphone_core.models import CapabilityStatus, DeviceInfo, ScreenshotArtifact
from winphone_core.observation import Observation
from winphone_core.runtime import DaemonInfo, read_info, spawn_detached

_WHY = "Run winphone daemon run to see why"

# (method, path, JSON body bytes or None) -> (HTTP status, response bytes)
Send = Callable[[str, str, bytes | None], tuple[int, bytes]]


def http_sender(port: int, token: str, timeout: float) -> Send:
    def send(method: str, path: str, payload: bytes | None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            headers = {"Authorization": f"Bearer {token}"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    return send


class DaemonClient:
    def __init__(self, send: Send) -> None:
        self._send = send

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = json.dumps(body).encode() if body is not None else None
        try:
            status, raw = self._send(method, path, payload)
        except (OSError, http.client.HTTPException) as e:
            raise DaemonUnavailable(f"lost the connection to winphoned: {type(e).__name__}", hint=_WHY) from e
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if 200 <= status < 300:
            return data
        if status == 401:
            raise DaemonUnavailable("winphoned rejected the token (stale daemon file?)",
                                    hint="Run winphone daemon stop, then retry")
        if isinstance(data, dict) and "error" in data and "message" in data:
            raise error_from_dict(data)
        if status == 422 and isinstance(data, dict):
            details = data.get("detail") or []
            msgs = "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in details)
            raise InvalidRequest(f"invalid request: {msgs}")
        raise DaemonUnavailable(f"winphoned returned HTTP {status}", hint=_WHY)

    @staticmethod
    def _dev(device_id: str) -> str:
        return "/devices/" + quote(device_id, safe=":")

    def healthz(self) -> str:
        return str(self._call("GET", "/healthz")["version"])

    def devices(self) -> list[DeviceInfo]:
        return [DeviceInfo.model_validate(d) for d in self._call("GET", "/devices")]

    def resolve(self, device: str | None) -> DeviceInfo:
        return DeviceInfo.model_validate(self._call("POST", "/devices/resolve", {"device": device}))

    def connect(self, device_id: str) -> DeviceInfo:
        return DeviceInfo.model_validate(self._call("POST", self._dev(device_id) + "/connect"))

    def disconnect(self, device_id: str) -> None:
        self._call("POST", self._dev(device_id) + "/disconnect")

    def capabilities(self, device_id: str) -> list[CapabilityStatus]:
        return [CapabilityStatus.model_validate(c)
                for c in self._call("GET", self._dev(device_id) + "/capabilities")]

    def screenshot(self, device_id: str) -> ScreenshotArtifact:
        return ScreenshotArtifact.model_validate(self._call("POST", self._dev(device_id) + "/screenshot"))

    def view(self, device_id: str) -> int:
        return int(self._call("POST", self._dev(device_id) + "/view")["pid"])

    def observe(self, device_id: str, screenshot: bool = False) -> Observation:
        return Observation.model_validate(self._call("POST", self._dev(device_id) + "/observe",
                                                     {"screenshot": screenshot}))

    def act(self, device_id: str, action: Action, wait: Wait | None = None,
            idempotency_key: str | None = None) -> Receipt:
        body = {"action": action.model_dump(mode="json", exclude_none=True),
                "wait": wait.model_dump(mode="json", exclude_none=True) if wait else None,
                "idempotency_key": idempotency_key}
        return Receipt.model_validate(self._call("POST", self._dev(device_id) + "/act", body))

    def apps(self, device_id: str, include_system: bool = False) -> list[str]:
        query = "?all=true" if include_system else ""
        return [str(p) for p in self._call("GET", self._dev(device_id) + "/apps" + query)]

    def logs(self, device_id: str, lines: int = 200, contains: str | None = None) -> list[str]:
        query = urlencode({"lines": lines, **({"contains": contains} if contains else {})})
        return [str(x) for x in self._call("GET", f"{self._dev(device_id)}/logs?{query}")]

    def crashes(self, device_id: str) -> list[str]:
        return [str(x) for x in self._call("GET", self._dev(device_id) + "/crashes")]

    def pull_crash(self, device_id: str, name: str) -> str:
        return str(self._call("POST", self._dev(device_id) + "/crashes/pull", {"name": name})["path"])

    def pair_wifi(self, host: str, port: int, code: str) -> None:
        self._call("POST", "/wifi/pair", {"host": host, "port": port, "code": code})

    def connect_wifi(self, host: str, port: int) -> DeviceInfo:
        return DeviceInfo.model_validate(self._call("POST", "/wifi/connect", {"host": host, "port": port}))

    def shutdown(self) -> None:
        self._call("POST", "/shutdown")


def default_spawn() -> None:
    spawn_detached([sys.executable, "-m", "winphone_cli", "daemon", "run"])


def _try(info: DaemonInfo, call_timeout: float) -> DaemonClient | None:
    try:
        DaemonClient(http_sender(info.port, info.token, timeout=2)).healthz()
    except DaemonUnavailable:
        return None
    return DaemonClient(http_sender(info.port, info.token, timeout=call_timeout))


def connect_daemon(*, autostart: bool = True, spawn: Callable[[], None] = default_spawn,
                   timeout: float = 15.0, call_timeout: float = 60.0) -> DaemonClient:
    """A client for the running daemon. A missing, dead or foreign daemon.json starts a fresh daemon."""
    info = read_info()
    if info is not None and (client := _try(info, call_timeout)) is not None:
        return client
    if not autostart:
        raise DaemonUnavailable("winphoned is not running", hint="Run winphone daemon start")
    spawn()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.2)
        info = read_info()
        if info is not None and (client := _try(info, call_timeout)) is not None:
            return client
    raise DaemonUnavailable("winphoned did not start", hint=_WHY)
