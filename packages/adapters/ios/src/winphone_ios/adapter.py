"""iPhone from Windows, without a Mac (design §7 Mode A).

Discovery and trust: Apple's usbmuxd, directly (milliseconds). Apps, logs, crash reports: go-ios
(https://github.com/danielpaulus/go-ios, MIT), pinned by `winphone setup`. Everything Apple reserves for
its developer tools raises CapabilityUnsupported with the reason; nothing is emulated.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, NoReturn

from winphone_core.errors import AdapterError, CapabilityUnsupported, DeviceNotFound, InvalidRequest, ToolMissing
from winphone_core.models import Capability, CapabilityStatus, DeviceInfo, Key, Platform, UiSnapshot
from winphone_core.tools import resolve

from winphone_ios import usbmux
from winphone_ios.mapping import REASONS, device_info_from_ios, ios_capabilities

UDID_RE = re.compile(r"[0-9A-Fa-f-]{20,64}", re.ASCII)
ABSENT_RETRY_S = 10.0  # without Apple's service, look again only this often (each try costs ~0.3 s)
LOG_WINDOW_S = 5.0
_GLOB = set("*?[]")


def _last_error(stderr: str) -> str:
    """go-ios logs JSON lines on stderr; the last ERROR line says what went wrong."""
    for line in reversed(stderr.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("level") == "ERROR":
            return f"{entry.get('msg', '')}: {entry['err']}" if entry.get("err") else str(entry.get("msg", ""))
    tail = [line for line in stderr.splitlines() if line.strip()]
    return tail[-1][:200] if tail else "no details"


def _json(out: str) -> Any:
    lines = [line for line in out.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        raise AdapterError("unexpected output from go-ios") from None


class WindowsIOSAdapter:
    platform = Platform.IOS

    def __init__(self, address: tuple[str, int] = usbmux.USBMUXD) -> None:
        self._address = address
        self._absent_until = 0.0
        self._values: dict[str, dict[str, str]] = {}  # ProductType/Version do not change while plugged in
        self._lock = threading.Lock()

    # --- discovery -------------------------------------------------------------------------------

    def discover(self) -> list[DeviceInfo]:
        if time.monotonic() < self._absent_until:
            return []
        try:
            devices = usbmux.list_devices(address=self._address)
        except usbmux.MuxUnavailable:
            self._absent_until = time.monotonic() + ABSENT_RETRY_S
            return []
        best: dict[str, usbmux.MuxDevice] = {}
        for d in devices:  # the same iPhone can be listed over USB and the network: prefer USB
            if d.udid not in best or d.connection == "USB":
                best[d.udid] = d
        found = []
        for d in best.values():
            with self._lock:
                values = self._values.get(d.udid)
            if values is None:
                values = usbmux.lockdown_values(d.device_id, ["ProductType", "ProductVersion"], address=self._address)
                if values:
                    with self._lock:
                        self._values[d.udid] = values
            trusted = usbmux.has_pair_record(d.udid, address=self._address)
            found.append(device_info_from_ios(d.udid, d.connection, values, trusted))
        return found

    def capabilities(self, serial: str) -> list[CapabilityStatus]:
        info = next((d for d in self.discover() if d.serial == serial), None)
        if info is None:
            raise DeviceNotFound(f"no device ios:{serial}")
        return ios_capabilities(info.state, resolve("go-ios").path is not None)

    # --- go-ios ----------------------------------------------------------------------------------

    def _exe(self) -> Path:
        tool = resolve("go-ios")
        if tool.path is None:
            raise ToolMissing("go-ios is not installed", hint="Run winphone setup")
        return tool.path

    def _go_ios(self, args: list[str], serial: str, timeout: float = 60.0) -> str:
        if not UDID_RE.fullmatch(serial):
            raise InvalidRequest(f"not an iPhone UDID: {serial!r}")
        try:
            r = subprocess.run([str(self._exe()), *args, f"--udid={serial}"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"go-ios {args[0]} timed out after {timeout:.0f} s") from None
        except OSError as e:
            raise AdapterError(f"could not run go-ios: {e.strerror}", hint="Run winphone doctor") from None
        if r.returncode != 0:
            raise AdapterError(f"go-ios {args[0]} failed: {_last_error(r.stderr or '')}")
        return r.stdout or ""

    def list_apps(self, serial: str, include_system: bool) -> list[str]:
        data = _json(self._go_ios(["apps", "--all"] if include_system else ["apps"], serial))
        return sorted({str(a["CFBundleIdentifier"]) for a in data or [] if "CFBundleIdentifier" in a})

    def _syslog_cmd(self, serial: str) -> list[str]:
        return [str(self._exe()), "syslog", f"--udid={serial}"]

    def logs(self, serial: str, lines: int, contains: str | None) -> list[str]:
        """The iPhone streams new log lines only: collect up to `lines` within LOG_WINDOW_S."""
        if not UDID_RE.fullmatch(serial):
            raise InvalidRequest(f"not an iPhone UDID: {serial!r}")
        proc = subprocess.Popen(self._syslog_cmd(serial), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace")
        q: queue.Queue[str | None] = queue.Queue()
        stream = proc.stdout

        def pump() -> None:
            for raw in stream or []:
                q.put(raw)
            q.put(None)  # the stream ended (iPhone unplugged, go-ios exited): stop waiting

        threading.Thread(target=pump, daemon=True).start()
        needle = contains.lower() if contains else None
        out: list[str] = []
        deadline = time.monotonic() + LOG_WINDOW_S
        try:
            while len(out) < lines and (left := deadline - time.monotonic()) > 0:
                try:
                    raw = q.get(timeout=left)
                except queue.Empty:
                    break
                if raw is None:
                    break
                try:
                    entry = json.loads(raw)
                    text = str(entry.get("msg", raw)) if isinstance(entry, dict) else raw
                except ValueError:
                    text = raw
                text = text.rstrip("\n")
                if text and (needle is None or needle in text.lower()):
                    out.append(text)
        finally:
            proc.kill()
            proc.wait(timeout=5)
        return out

    def crash_reports(self, serial: str) -> list[str]:
        data = _json(self._go_ios(["crash", "ls"], serial)) or {}
        return [str(f) for f in data.get("files") or []]

    def pull_crash_report(self, serial: str, name: str, dest: Path) -> Path:
        if name not in self.crash_reports(serial):
            raise InvalidRequest(f"{name!r} is not one of this iPhone's crash reports", hint="Run winphone crashes")
        if _GLOB & set(name):
            raise InvalidRequest("crash reports whose names contain *, ?, [ or ] cannot be copied")
        dest.mkdir(parents=True, exist_ok=True)
        self._go_ios(["crash", "cp", name, str(dest)], serial, timeout=120)
        path = dest / name
        if not path.is_file():
            raise AdapterError(f"go-ios reported success but {name} was not copied")
        return path

    # --- what Windows cannot do on an iPhone -----------------------------------------------------

    def _unsupported(self, capability: Capability) -> NoReturn:
        raise CapabilityUnsupported(f"iPhone {capability.value}: {REASONS[capability]}")

    def screenshot(self, serial: str) -> bytes:
        self._unsupported(Capability.SCREENSHOT)

    def live_view(self, serial: str) -> int:
        self._unsupported(Capability.LIVE_VIEW)

    def pair(self, host: str, port: int, code: str) -> None:
        self._unsupported(Capability.WIRELESS)

    def connect_wifi(self, host: str, port: int) -> str:
        self._unsupported(Capability.WIRELESS)

    def disconnect_wifi(self, serial: str) -> None:
        """Nothing to drop: WINPHONE never opens a network link to an iPhone."""

    def ui_snapshot(self, serial: str) -> UiSnapshot:
        self._unsupported(Capability.OBSERVE_TREE)

    def tap(self, serial: str, x: int, y: int) -> None:
        self._unsupported(Capability.TAP)

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._unsupported(Capability.SWIPE)

    def type_text(self, serial: str, text: str) -> None:
        self._unsupported(Capability.TYPE)

    def press(self, serial: str, key: Key) -> None:
        self._unsupported(Capability.KEYS)

    def launch_app(self, serial: str, package: str) -> None:
        self._unsupported(Capability.LAUNCH_APP)

    def install_app(self, serial: str, apk: Path) -> None:
        self._unsupported(Capability.INSTALL_APP)

    def record(self, serial: str, seconds: int, dest: Path) -> Path:
        self._unsupported(Capability.RECORD)

    def release(self, serial: str) -> None:
        """No helper runs on the iPhone."""
