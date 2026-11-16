"""Test doubles. Never imported outside tests/."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable

from fastapi.testclient import TestClient
from winphone_core.models import Capability, CapabilityStatus, DeviceInfo, DeviceState, Platform, Transport


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
                 unsupported: dict[Capability, str] | None = None) -> None:
        self.devices = {d.serial: d for d in devices or []}
        self.unsupported = unsupported or {}
        self.png = make_png(4, 8)
        self.calls: list[tuple[str, ...]] = []

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
