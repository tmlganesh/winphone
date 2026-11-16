"""Test doubles. Never imported outside tests/."""

from __future__ import annotations

import html
import struct
import zlib
from collections.abc import Callable

from fastapi.testclient import TestClient
from winphone_core.models import (
    Capability,
    CapabilityStatus,
    DeviceInfo,
    DeviceState,
    Key,
    Platform,
    Transport,
    UiSnapshot,
)

_NODE_DEFAULTS = {
    "text": "", "resource-id": "", "class": "android.widget.TextView", "package": "com.example",
    "content-desc": "", "checkable": "false", "checked": "false", "clickable": "false", "enabled": "true",
    "focusable": "false", "focused": "false", "scrollable": "false", "password": "false", "selected": "false",
    "bounds": "[0,0][100,100]",
}


def ui_xml(*nodes: dict[str, str], rotation: int = 0) -> str:
    """A flat UI-hierarchy dump, in uiautomator's format, from attribute dicts."""
    body = "".join("<node " + " ".join(f'{k}="{html.escape(v, quote=True)}"'
                                       for k, v in {**_NODE_DEFAULTS, **n}.items()) + "/>" for n in nodes)
    return f'<hierarchy rotation="{rotation}">{body}</hierarchy>'


SIGN_IN = {"text": "Sign in", "resource-id": "com.example:id/login", "class": "android.widget.Button",
           "clickable": "true", "bounds": "[340,1580][740,1660]"}
PASSWORD = {"resource-id": "com.example:id/password", "class": "android.widget.EditText", "password": "true",
            "clickable": "true", "focusable": "true", "bounds": "[100,700][980,820]"}
LOGIN = ui_xml(PASSWORD, SIGN_IN)
LOGIN_SHIFTED = ui_xml({"text": "Banner", "bounds": "[0,100][1080,200]"}, PASSWORD,
                       {**SIGN_IN, "bounds": "[340,1780][740,1860]"})
HOME = ui_xml({"text": "Welcome back", "bounds": "[100,300][980,400]"})


def make_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    body = chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def sender_for(tc: TestClient) -> Callable[[str, str, bytes | None], tuple[int, bytes]]:
    """DaemonClient transport over an in-memory FastAPI app."""

    def send(method: str, path: str, payload: bytes | None) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        r = tc.request(method, path, content=payload, headers=headers)
        return r.status_code, r.content

    return send


def dev(serial: str, state: DeviceState = DeviceState.ONLINE,
        transport: Transport = Transport.USB) -> DeviceInfo:
    return DeviceInfo(id=f"android:{serial}", platform=Platform.ANDROID, serial=serial, state=state,
                      transport=transport, model="Fake", os_version="14", sdk=34)


class FakeAdapter:
    platform = Platform.ANDROID

    def __init__(self, devices: list[DeviceInfo] | None = None,
                 unsupported: dict[Capability, str] | None = None,
                 screens: list[tuple[str, str | None]] | None = None) -> None:
        self.devices = {d.serial: d for d in devices or []}
        self.unsupported = unsupported or {}
        self.png = make_png(4, 8)
        self.calls: list[tuple[str, ...]] = []
        # Scripted screens: each input moves to the next one (when advance_on_input), the last one stays.
        self.screens = screens or [(LOGIN, "com.example"), (HOME, "com.example")]
        self.index = 0
        self.advance_on_input = True
        self.input_error: Exception | None = None
        self.type_error: Exception | None = None
        self.snapshot_error: Exception | None = None
        self.break_snapshots_after_input = False  # the phone vanishes right after an input lands

    def ui_snapshot(self, serial: str) -> UiSnapshot:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        xml, app = self.screens[min(self.index, len(self.screens) - 1)]
        return UiSnapshot(xml=xml, foreground_app=app, width=1080, height=2400)

    def _input(self, *call: object) -> None:
        self.calls.append(tuple(str(c) for c in call))
        if self.input_error is not None:
            raise self.input_error
        if self.advance_on_input:
            self.index += 1
        if self.break_snapshots_after_input:
            from winphone_core.errors import AdapterError

            self.snapshot_error = AdapterError("phone gone")

    def tap(self, serial: str, x: int, y: int) -> None:
        self._input("tap", x, y)

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._input("swipe", x1, y1, x2, y2)

    def type_text(self, serial: str, text: str) -> None:
        if self.type_error is not None:
            raise self.type_error
        self._input("type", len(text))

    def press(self, serial: str, key: Key) -> None:
        self._input("press", key.value)

    def launch_app(self, serial: str, package: str) -> None:
        self._input("launch", package)

    def list_apps(self, serial: str, include_system: bool) -> list[str]:
        return ["com.example"]

    def release(self, serial: str) -> None:
        self.calls.append(("release", serial))

    def discover(self) -> list[DeviceInfo]:
        return list(self.devices.values())

    def capabilities(self, serial: str) -> list[CapabilityStatus]:
        return [CapabilityStatus(capability=c, supported=c not in self.unsupported,
                                 reason=self.unsupported.get(c)) for c in Capability]

    def screenshot(self, serial: str) -> bytes:
        self.calls.append(("screenshot", serial))
        return self.png

    def live_view(self, serial: str) -> int:
        self.calls.append(("live_view", serial))
        return 4242

    def pair(self, host: str, port: int, code: str) -> None:
        self.calls.append(("pair", host, str(port)))

    def connect_wifi(self, host: str, port: int) -> str:
        serial = f"{host}:{port}"
        self.devices[serial] = dev(serial, transport=Transport.WIFI)
        return serial

    def disconnect_wifi(self, serial: str) -> None:
        self.calls.append(("disconnect_wifi", serial))
        self.devices.pop(serial, None)
