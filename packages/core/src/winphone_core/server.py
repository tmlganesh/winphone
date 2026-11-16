"""winphoned HTTP API. Local only: every request must carry the token, target 127.0.0.1:<port>,
and come either without an Origin or from the Tauri app (blocks browser pages and DNS rebinding)."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from winphone_core import __version__
from winphone_core.actions import Action, Receipt, Wait
from winphone_core.errors import AdapterError, DeviceNotFound, WinphoneError
from winphone_core.models import CapabilityStatus, DeviceInfo, ScreenshotArtifact
from winphone_core.observation import Observation
from winphone_core.orchestrator import Orchestrator

TAURI_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"})
_HOST = r"^[A-Za-z0-9][A-Za-z0-9.\-]*$"  # IPv4 or hostname; never starts with '-', so never an adb flag


class ResolveBody(BaseModel):
    device: str | None = None


class WifiBody(BaseModel):
    host: str = Field(pattern=_HOST, max_length=253)
    port: int = Field(ge=1, le=65535)


class PairBody(WifiBody):
    code: str = Field(pattern=r"^\d{6}$")


class ObserveBody(BaseModel):
    screenshot: bool = False


class ActBody(BaseModel):
    action: Action
    wait: Wait | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


def _deny(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, "hint": None}, status_code=status)


def create_app(orch: Orchestrator, token: str, port: int, on_shutdown: Callable[[], None]) -> FastAPI:
    app = FastAPI(title="winphoned", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.last_request = time.monotonic()
    expected_host = f"127.0.0.1:{port}"
    expected_auth = f"Bearer {token}".encode()

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.headers.get("host") != expected_host:
            return _deny(403, "bad_host", "requests must target 127.0.0.1")
        origin = request.headers.get("origin")
        if origin is not None and origin not in TAURI_ORIGINS:
            return _deny(403, "bad_origin", "cross-origin requests are refused")
        if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected_auth):
            return _deny(401, "unauthorized", "missing or wrong daemon token")
        app.state.last_request = time.monotonic()
        return await call_next(request)

    @app.exception_handler(WinphoneError)
    async def winphone_error(request: Request, exc: WinphoneError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=404 if isinstance(exc, DeviceNotFound) else 409)

    # FastAPI's default 422 echoes each invalid input back, and an action's input can hold typed text.
    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    # Anything else is a bug or an unexpected tool failure: report its type only, never its text
    # (a subprocess error's text can carry argv, e.g. a pairing code).
    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        err = AdapterError(f"internal error ({type(exc).__name__})",
                           hint="Run winphone daemon run to see why")
        return JSONResponse(err.to_dict(), status_code=500)

    # Handlers are plain `def`: FastAPI runs them in its thread pool, adapters stay synchronous.
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/devices")
    def devices() -> list[DeviceInfo]:
        return orch.devices()

    @app.post("/devices/resolve")
    def resolve(body: ResolveBody) -> DeviceInfo:
        return orch.resolve(body.device)

    @app.post("/devices/{device_id}/connect")
    def connect(device_id: str) -> DeviceInfo:
        return orch.connect(device_id)

    @app.post("/devices/{device_id}/disconnect")
    def disconnect(device_id: str) -> dict[str, bool]:
        orch.disconnect(device_id)
        return {"ok": True}

    @app.get("/devices/{device_id}/capabilities")
    def capabilities(device_id: str) -> list[CapabilityStatus]:
        return orch.capabilities(device_id)

    @app.post("/devices/{device_id}/screenshot")
    def screenshot(device_id: str) -> ScreenshotArtifact:
        return orch.screenshot(device_id)

    @app.post("/devices/{device_id}/view")
    def view(device_id: str) -> dict[str, int]:
        return {"pid": orch.live_view(device_id)}

    @app.post("/devices/{device_id}/observe")
    def observe(device_id: str, body: ObserveBody) -> Observation:
        return orch.observe(device_id, screenshot=body.screenshot)

    @app.post("/devices/{device_id}/act")
    def act(device_id: str, body: ActBody) -> Receipt:
        return orch.act(device_id, body.action, wait=body.wait, idempotency_key=body.idempotency_key)

    @app.get("/devices/{device_id}/apps")
    def apps(device_id: str, all: bool = False) -> list[str]:  # noqa: A002 - the query parameter's name
        return orch.apps(device_id, include_system=all)

    @app.post("/wifi/pair")
    def pair(body: PairBody) -> dict[str, bool]:
        orch.pair_wifi(body.host, body.port, body.code)
        return {"ok": True}

    @app.post("/wifi/connect")
    def connect_wifi(body: WifiBody) -> DeviceInfo:
        return orch.connect_wifi(body.host, body.port)

    @app.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        on_shutdown()
        return {"ok": True}

    return app
