"""The single owner of device access. CLI, MCP and the desktop app reach phones only through here."""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from winphone_core import actions
from winphone_core.actions import Action, Receipt, Wait
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
from winphone_core.observation import Observation, ObservationStore, normalize

_RECEIPTS_KEPT = 128


class Orchestrator:
    def __init__(self, adapters: Sequence[DeviceAdapter], artifacts: ArtifactStore) -> None:
        self._adapters = list(adapters)
        self._artifacts = artifacts
        self._held: set[str] = set()
        self._lock = threading.Lock()
        self._store = ObservationStore()
        self._ids = itertools.count(1)
        self._device_locks: dict[str, threading.RLock] = {}
        self._receipts: OrderedDict[str, Receipt] = OrderedDict()  # idempotency keys
        self._touched: dict[str, tuple[DeviceAdapter, str]] = {}  # phones that may run a helper

    def _device_lock(self, device_id: str) -> threading.RLock:
        """One phone does one thing at a time: observe and act never interleave on it."""
        with self._lock:
            return self._device_locks.setdefault(device_id, threading.RLock())

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
        with self._lock:
            self._touched.pop(d.id, None)
        a.release(d.serial)
        if d.transport is Transport.WIFI:
            a.disconnect_wifi(d.serial)

    def close(self) -> None:
        """Stop every on-phone helper this daemon started (the daemon is exiting)."""
        with self._lock:
            touched, self._touched = list(self._touched.values()), {}
        for adapter, serial in touched:
            try:
                adapter.release(serial)
            except WinphoneError:
                pass  # the phone may already be gone

    def _observe(self, a: DeviceAdapter, d: DeviceInfo) -> Observation:
        """Read the screen without publishing it: only observe() decides what a bare ref means."""
        snap = a.ui_snapshot(d.serial)
        with self._lock:
            self._touched[d.id] = (a, d.serial)
        return normalize(snap.xml, device_id=d.id, observation_id=f"o{next(self._ids)}",
                         foreground_app=snap.foreground_app, width=snap.width, height=snap.height)

    def observe(self, device_id: str, screenshot: bool = False) -> Observation:
        a, d = self._ready(device_id, Capability.OBSERVE_TREE)
        with self._device_lock(d.id):
            obs = self._observe(a, d)
            self._store.put(obs)
        if screenshot:
            obs = obs.model_copy(update={"screenshot_path": self.screenshot(d.id).path})
        return obs

    def act(self, device_id: str, action: Action, wait: Wait | None = None,
            idempotency_key: str | None = None) -> Receipt:
        a, d = self._ready(device_id, actions.CAPABILITY[action.kind])
        with self._device_lock(d.id):
            if idempotency_key is not None and idempotency_key in self._receipts:
                return self._receipts[idempotency_key]

            def previous(observation_id: str | None) -> Observation | None:
                if observation_id is None:
                    return self._store.latest(d.id)
                return self._store.get(d.id, observation_id)

            receipt = actions.run(action, wait, adapter=a, serial=d.serial, observe=lambda: self._observe(a, d),
                                  previous=previous, record=lambda obs: self._store.put(obs, latest=False))
            if idempotency_key is not None:
                self._receipts[idempotency_key] = receipt
                while len(self._receipts) > _RECEIPTS_KEPT:
                    self._receipts.popitem(last=False)
        return receipt

    def apps(self, device_id: str, include_system: bool = False) -> list[str]:
        a, d = self._ready(device_id, Capability.LIST_APPS)
        return a.list_apps(d.serial, include_system)

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
