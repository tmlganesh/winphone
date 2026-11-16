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
from winphone_core.errors import AdapterError, DeviceNotFound, PermissionDenied, WinphoneError
from winphone_core.models import Capability, CapabilityStatus, DeviceInfo, ScreenshotArtifact
from winphone_core.observation import Element, Observation
from winphone_core.orchestrator import Orchestrator
from winphone_core.permissions import CLIENT_HEADER, ClientKind, PermissionManager, Tier, client_kind, quoted

TAURI_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"})
_HOST = r"^[A-Za-z0-9][A-Za-z0-9.\-]*$"  # IPv4 or hostname; never starts with '-', so never an adb flag


class ResolveBody(BaseModel):
    device: str | None = None


class WifiBody(BaseModel):
    host: str = Field(pattern=_HOST, max_length=253)
    port: int = Field(ge=1, le=65535)


class PairBody(WifiBody):
    code: str = Field(pattern=r"^\d{6}$")


class PullBody(BaseModel):
    name: str = Field(min_length=1, max_length=512)


class ObserveBody(BaseModel):
    screenshot: bool = False


class ActBody(BaseModel):
    action: Action
    wait: Wait | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class InstallBody(BaseModel):
    apk: str = Field(min_length=1, max_length=1024)


class RecordBody(BaseModel):
    seconds: int = Field(default=10, ge=1, le=60)


class AuthorizeBody(BaseModel):
    device: str | None = None
    minutes: int = Field(default=30, ge=1, le=240)


class RevokeBody(BaseModel):
    device: str | None = None


def _deny(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, "hint": None}, status_code=status)


def _labels(action: Action, el: Element | None, fresh: Observation) -> list[str]:
    """Every text and description that the input could be aimed at: the target itself, anything drawn
    inside it, and anything under the tapped point (a point tap has no target element)."""
    point = el.center if el is not None else (action.target.point if action.target is not None else None)
    found: list[str | None] = [el.text, el.desc] if el is not None else []
    for e in fresh.elements:
        x1, y1, x2, y2 = e.bounds
        inside = el is not None and el.bounds[0] <= x1 and el.bounds[1] <= y1 and x2 <= el.bounds[2] \
            and y2 <= el.bounds[3]
        under = point is not None and x1 <= point[0] < x2 and y1 <= point[1] < y2
        if inside or under:
            found += [e.text, e.desc]
    return list(dict.fromkeys(s for s in found if s))


def _describe(action: Action, el: Element | None, app: str | None, labels: list[str]) -> str:
    """What the human is asked about. Typed text never appears (only its length), nor the field's text."""
    where = f" in {app}" if app else ""
    if action.kind == "type":
        name = (el.desc or (el.resource_id or "").split(":id/")[-1]) if el is not None else ""
        return f"type {len(action.text or '')} characters into {quoted(name) or 'the focused field'}{where}"
    label = quoted(labels[0]) if labels else ""
    if action.kind == "launch":
        return f"launch {action.package}"
    if action.kind == "press":
        return f"press {action.key.value if action.key else '?'}{where}"
    return f"{action.kind} {label}{where}".replace("  ", " ")


def _client(request: Request) -> ClientKind:
    return client_kind(request.headers.get(CLIENT_HEADER))


def create_app(orch: Orchestrator, token: str, port: int, on_shutdown: Callable[[], None],
               permissions: PermissionManager) -> FastAPI:
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
    def act(device_id: str, body: ActBody, request: Request) -> Receipt:
        client = _client(request)

        def authorize(action: Action, el: Element | None, fresh: Observation) -> bool:
            labels = _labels(action, el, fresh)
            tier = permissions.policy.tier_of(action.kind, fresh.foreground_app, " | ".join(labels) or None)
            details = {"kind": action.kind} | ({"chars": len(action.text)} if action.text is not None else {})
            return permissions.require(client, fresh.device_id, tier,
                                       _describe(action, el, fresh.foreground_app, labels), **details)

        receipt = orch.act(device_id, body.action, wait=body.wait, idempotency_key=body.idempotency_key,
                           authorize=authorize)
        permissions.audit.write(event="result", client=client.value, device=receipt.device_id, kind=receipt.kind,
                                outcome=receipt.outcome, action_id=receipt.action_id)
        return receipt

    @app.post("/devices/{device_id}/wait")
    def wait(device_id: str, body: Wait) -> Receipt:
        return orch.wait(device_id, body)

    @app.post("/devices/{device_id}/install")
    def install(device_id: str, body: InstallBody, request: Request) -> dict[str, str]:
        # Check everything before asking: never ask a human to approve something that cannot run. The file is
        # copied first, so what gets installed is exactly what they approved.
        d = orch.require(orch.resolve(device_id).id, Capability.INSTALL_APP)
        staged = orch.stage_apk(d.id, body.apk)
        name = staged.name
        source = "".join(c if c.isprintable() else " " for c in body.apk)
        permissions.require(_client(request), d.id, Tier.SENSITIVE,
                            f"install the app {quoted(name)}\nFile: {source} ({staged.stat().st_size} bytes)",
                            kind="install")
        orch.install_staged(d.id, staged)
        permissions.audit.write(event="result", client=_client(request).value, device=d.id, kind="install",
                                outcome="installed", app=name)
        return {"installed": name}

    @app.post("/devices/{device_id}/record")
    def record(device_id: str, body: RecordBody) -> dict[str, str]:
        return {"path": orch.record(device_id, body.seconds)}

    @app.post("/authorize")
    def authorize_device(body: AuthorizeBody, request: Request) -> dict[str, Any]:
        d = orch.resolve(body.device)
        grant = permissions.request_grant(_client(request), d.id, body.minutes)
        return {"device": grant.device_id, "expires_at": grant.expires_at}

    @app.post("/revoke")
    def revoke(body: RevokeBody) -> dict[str, int]:
        device = body.device
        if device is not None:
            try:
                device = orch.resolve(device).id
            except WinphoneError:
                pass  # an offline phone's grant can still be revoked by its id
        return {"revoked": permissions.revoke(device)}

    @app.get("/grants")
    def grants() -> list[dict[str, Any]]:
        return [{"device": g.device_id, "expires_at": g.expires_at} for g in permissions.grants()]

    @app.get("/devices/{device_id}/apps")
    def apps(device_id: str, all: bool = False) -> list[str]:  # noqa: A002 - the query parameter's name
        return orch.apps(device_id, include_system=all)

    @app.get("/devices/{device_id}/logs")
    def logs(device_id: str, lines: int = 200, contains: str | None = None) -> list[str]:
        return orch.logs(device_id, lines=lines, contains=contains)

    @app.get("/devices/{device_id}/crashes")
    def crashes(device_id: str) -> list[str]:
        return orch.crash_reports(device_id)

    @app.post("/devices/{device_id}/crashes/pull")
    def pull_crash(device_id: str, body: PullBody) -> dict[str, str]:
        return {"path": orch.pull_crash_report(device_id, body.name)}

    def human_only(request: Request, what: str) -> None:
        if _client(request) is not ClientKind.HUMAN:
            raise PermissionDenied(f"{what} is for the human", hint="Run it with winphone in a console window")

    @app.post("/wifi/pair")
    def pair(body: PairBody, request: Request) -> dict[str, bool]:
        human_only(request, "Wi-Fi pairing")
        orch.pair_wifi(body.host, body.port, body.code)
        return {"ok": True}

    @app.post("/wifi/connect")
    def connect_wifi(body: WifiBody, request: Request) -> DeviceInfo:
        human_only(request, "Connecting a phone over Wi-Fi")
        return orch.connect_wifi(body.host, body.port)

    @app.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        on_shutdown()
        return {"ok": True}

    return app
