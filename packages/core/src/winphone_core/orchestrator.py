"""The single owner of device access. CLI, MCP and the desktop app reach phones only through here."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from winphone_core.adapter import DeviceAdapter
from winphone_core.artifacts import ArtifactStore, png_size
from winphone_core.errors import (
    CapabilityUnsupported,
    DeviceAmbiguous,
    DeviceNotFound,
    DeviceNotReady,
    WinphoneError,
)
from winphone_core.models import (
    NOT_READY_HINTS,
    Capability,
    CapabilityStatus,
    DeviceInfo,
    DeviceState,
    Platform,
    ScreenshotArtifact,
    Transport,
)


class Orchestrator:
    def __init__(self, adapters: Sequence[DeviceAdapter], artifacts: ArtifactStore) -> None:
        self._adapters = list(adapters)
        self._artifacts = artifacts
        self._held: set[str] = set()
        self._lock = threading.Lock()

    # Discovery is never cached: a phone can vanish between two calls.
    def _discover(self) -> list[tuple[DeviceAdapter, DeviceInfo]]:
        found = [(a, d) for a in self._adapters for d in a.discover()]
        with self._lock:
            return [(a, d.model_copy(update={"held": d.id in self._held})) for a, d in found]

    def devices(self) -> list[DeviceInfo]:
        return [d for _, d in self._discover()]

    def _find(self, device_id: str) -> tuple[DeviceAdapter, DeviceInfo]:
        for a, d in self._discover():
            if device_id in (d.id, d.serial):
                return a, d
        raise DeviceNotFound(f"no device {device_id}", hint="Run winphone devices to see what is connected")

    def _ready(self, device_id: str, capability: Capability | None = None
               ) -> tuple[DeviceAdapter, DeviceInfo]:
        a, d = self._find(device_id)
        if d.state is not DeviceState.ONLINE:
            raise DeviceNotReady(f"{d.id} is {d.state.value}", hint=NOT_READY_HINTS.get(d.state))
        if capability is not None:
            status = next((c for c in a.capabilities(d.serial) if c.capability is capability), None)
            if status is None or not status.supported:
                reason = status.reason if status else "not reported by the adapter"
                raise CapabilityUnsupported(f"{d.id} cannot {capability.value}: {reason}")
        return a, d

    def resolve(self, selector: str | None) -> DeviceInfo:
        if selector:
            return self._find(selector)[1]
        found = self.devices()
        online = [d for d in found if d.state is DeviceState.ONLINE]
        if not online and found:
            states = ", ".join(f"{d.id} is {d.state.value}" for d in found)
            raise DeviceNotReady(f"no phone is online: {states}", hint=NOT_READY_HINTS.get(found[0].state))
        if not online:
            raise DeviceNotFound("no phone found", hint="Run winphone doctor")
        if len(online) > 1:
            ids = ", ".join(d.id for d in online)
            raise DeviceAmbiguous(f"{len(online)} phones are online: {ids}", hint="Pass --device <id>")
        return online[0]

    def connect(self, device_id: str) -> DeviceInfo:
        _, d = self._ready(device_id)
        with self._lock:
            self._held.add(d.id)
        return d.model_copy(update={"held": True})

    def disconnect(self, device_id: str) -> None:
        a, d = self._find(device_id)
        with self._lock:
            self._held.discard(d.id)
        if d.transport is Transport.WIFI:
            a.disconnect_wifi(d.serial)

    def held_count(self) -> int:
        """Holds on phones that are gone are dropped, so an unplugged phone cannot keep the daemon alive."""
        try:
            present: set[str] | None = {d.id for _, d in self._discover()}
        except WinphoneError:
            present = None  # adb unreachable: keep the holds rather than guess
        with self._lock:
            if present is not None:
                self._held &= present
            return len(self._held)

    def capabilities(self, device_id: str) -> list[CapabilityStatus]:
        a, d = self._find(device_id)
        return a.capabilities(d.serial)

    def screenshot(self, device_id: str) -> ScreenshotArtifact:
        a, d = self._ready(device_id, Capability.SCREENSHOT)
        png = a.screenshot(d.serial)
        width, height = png_size(png)
        path: Path = self._artifacts.save_screenshot(d.id, png)
        return ScreenshotArtifact(device_id=d.id, path=str(path), width=width, height=height,
                                  taken_at=datetime.now(UTC))

    def live_view(self, device_id: str) -> int:
        a, d = self._ready(device_id, Capability.LIVE_VIEW)
        return a.live_view(d.serial)

    # Wireless debugging is an Android (adb) feature.
    def _android(self) -> DeviceAdapter:
        return next(a for a in self._adapters if a.platform is Platform.ANDROID)

    def pair_wifi(self, host: str, port: int, code: str) -> None:
        self._android().pair(host, port, code)

    def connect_wifi(self, host: str, port: int) -> DeviceInfo:
        serial = self._android().connect_wifi(host, port)
        return self._find(serial)[1]
