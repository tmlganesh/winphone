"""Android adapter: adb (via adbutils and the pinned adb.exe), scrcpy for live view, and uiautomator2
for the UI tree, taps, swipes and text.

Docs: https://developer.android.com/tools/adb · https://github.com/Genymobile/scrcpy ·
https://github.com/openatx/uiautomator2

On Android 17, uiautomator2 3.7.1's deviceInfo/app_current are broken: never call `info` or
`app_current`. Measured choices (Phase 2 Task 1): tap/swipe/text through uiautomator2, keys through
`adb shell input keyevent`.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import adbutils
import uiautomator2
from winphone_core.errors import (
    AdapterError,
    CapabilityUnsupported,
    DeviceNotFound,
    InvalidRequest,
    OutcomeUnknown,
    ToolMissing,
)
from winphone_core.models import CapabilityStatus, DeviceInfo, Key, Platform, UiSnapshot
from winphone_core.paths import app_dir
from winphone_core.tools import resolve

from winphone_android.mapping import (
    CRASH_REPORTS_REASON,
    INPUT_DENIED_REASON,
    KEYCODES,
    PACKAGE_RE,
    PROP_KEYS,
    android_capabilities,
    device_info_from,
    display_size,
    parse_focus,
    parse_wm_size,
)


class AndroidAdapter:
    platform = Platform.ANDROID

    def __init__(self) -> None:
        adb = resolve("adb")
        if adb.path is None:
            raise ToolMissing("adb is not installed", hint="Run winphone setup")
        self._adb = str(adb.path)
        # adbutils and scrcpy must use the same adb, or each restarts the other's server.
        os.environ["ADBUTILS_ADB_PATH"] = self._adb
        subprocess.run([self._adb, "start-server"], capture_output=True, timeout=20, check=False)
        self._client = adbutils.AdbClient()
        self._props: dict[str, dict[str, str]] = {}
        self._props_lock = threading.Lock()  # the daemon calls discover() from several threads
        self._u2: dict[str, Any] = {}  # serial -> uiautomator2.Device; its helper starts on first use
        self._u2_lock = threading.Lock()
        self._sizes: dict[str, tuple[int, int]] = {}
        self._input_denied: set[str] = set()

    def _props_for(self, serial: str) -> dict[str, str]:
        with self._props_lock:
            cached = self._props.get(serial)
        if cached is not None:
            return cached
        cmd = "; ".join(f"getprop {k}" for k in PROP_KEYS)
        try:
            out = str(self._client.device(serial).shell(cmd, timeout=10))
        except adbutils.AdbError:
            return {}  # one slow phone must not hide the others; not cached, so retried next time
        props = dict(zip(PROP_KEYS, out.splitlines(), strict=False))
        with self._props_lock:
            self._props[serial] = props
        return props

    def discover(self) -> list[DeviceInfo]:
        try:
            listed = self._client.list()
        except adbutils.AdbError as e:
            raise AdapterError(f"adb server not reachable: {e}", hint="Run winphone doctor") from e
        infos = [device_info_from(i.serial, i.state, self._props_for(i.serial) if i.state == "device" else {})
                 for i in listed]
        present = {i.serial for i in infos}
        with self._props_lock:
            for gone in set(self._props) - present:
                self._props.pop(gone, None)
        return infos

    def capabilities(self, serial: str) -> list[CapabilityStatus]:
        info = next((d for d in self.discover() if d.serial == serial), None)
        if info is None:
            raise DeviceNotFound(f"no device android:{serial}")
        return android_capabilities(info.state, info.sdk, resolve("scrcpy").path is not None,
                                    input_denied=serial in self._input_denied)

    def screenshot(self, serial: str) -> bytes:
        try:
            r = subprocess.run([self._adb, "-s", serial, "exec-out", "screencap", "-p"],
                               capture_output=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"screencap on {serial} timed out after 30 s") from None
        except OSError as e:
            raise AdapterError(f"could not run adb: {e.strerror}", hint="Run winphone doctor") from None
        if r.returncode != 0:
            raise AdapterError(f"screencap failed on {serial}: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout

    def live_view(self, serial: str) -> int:
        scrcpy = resolve("scrcpy")
        if scrcpy.path is None:
            raise ToolMissing("scrcpy is not installed", hint="Run winphone setup")
        title = f"WINPHONE - {self._props.get(serial, {}).get('ro.product.model') or serial}"
        log = app_dir() / "logs" / f"scrcpy-{serial.replace(':', '_')}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as err:
            proc = subprocess.Popen(
                [str(scrcpy.path), "--serial", serial, "--window-title", title],
                env={**os.environ, "ADB": self._adb},
                stdin=subprocess.DEVNULL, stdout=err, stderr=subprocess.STDOUT,
            )
        time.sleep(2)
        if proc.poll() is not None:
            tail = log.read_text(errors="replace").strip().splitlines()[-5:]
            raise AdapterError(f"scrcpy exited with code {proc.returncode}", hint=" | ".join(tail) or None)
        return proc.pid

    def pair(self, host: str, port: int, code: str) -> None:
        # `from None` everywhere below: the subprocess errors carry argv, which holds the pairing code.
        try:
            r = subprocess.run([self._adb, "pair", f"{host}:{port}", code],
                               capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"pairing with {host}:{port} timed out after 30 s",
                               hint="Check the pairing port and code on the phone; both change each time"
                               ) from None
        except OSError as e:
            raise AdapterError(f"could not run adb: {e.strerror}", hint="Run winphone doctor") from None
        out = (r.stdout + r.stderr).strip()
        if "Successfully paired" not in out:
            raise AdapterError(f"pairing with {host}:{port} failed: {out.replace(code, '******')}",
                               hint="On the phone: Wireless debugging > Pair device with pairing code")

    def connect_wifi(self, host: str, port: int) -> str:
        addr = f"{host}:{port}"
        try:
            msg = self._client.connect(addr, timeout=10)
        except adbutils.AdbError as e:
            raise AdapterError(f"could not connect to {addr}: {e}") from e
        if not msg.startswith(("connected to", "already connected")):
            raise AdapterError(f"could not connect to {addr}: {msg}",
                               hint="Check the phone's IP and port under Wireless debugging")
        return addr

    def disconnect_wifi(self, serial: str) -> None:
        self._client.disconnect(serial)

    # --- UI tree -------------------------------------------------------------------------------

    def _u2_device(self, serial: str) -> Any:
        with self._u2_lock:
            dev = self._u2.get(serial)
            if dev is None:
                dev = uiautomator2.connect(serial)
                self._u2[serial] = dev
            return dev

    def _builtin_dump(self, serial: str) -> str:
        try:
            r = subprocess.run([self._adb, "-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                               capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"reading the screen of {serial} timed out after 30 s") from None
        start, end = r.stdout.find("<?xml"), r.stdout.rfind("</hierarchy>")
        if start < 0 or end < 0:
            raise AdapterError(f"could not read the screen of {serial}", hint=r.stdout.strip()[-120:] or None)
        return r.stdout[start:end + len("</hierarchy>")]

    def _physical_size(self, serial: str) -> tuple[int, int]:
        if serial not in self._sizes:
            try:
                self._sizes[serial] = parse_wm_size(str(self._client.device(serial).shell("wm size", timeout=10)))
            except (adbutils.AdbError, KeyError) as e:
                raise AdapterError(f"could not read the display size of {serial}: {type(e).__name__}") from None
        return self._sizes[serial]

    def _foreground(self, serial: str) -> str | None:
        try:
            return parse_focus(str(self._client.device(serial).shell("dumpsys window | grep mCurrentFocus",
                                                                     timeout=10)))
        except adbutils.AdbError:
            return None

    def ui_snapshot(self, serial: str) -> UiSnapshot:
        try:
            xml = str(self._u2_device(serial).dump_hierarchy())
        except Exception:  # noqa: BLE001 - any helper failure: fall back to Android's own (slower) dump
            xml = self._builtin_dump(serial)
        m = re.search(r'<hierarchy[^>]*\brotation="(\d)"', xml)
        width, height = display_size(self._physical_size(serial), int(m.group(1)) if m else 0)
        return UiSnapshot(xml=xml, foreground_app=self._foreground(serial), width=width, height=height)

    # --- input ---------------------------------------------------------------------------------

    def _input_failed(self, serial: str, what: str, e: BaseException) -> NoReturn:
        """Classify a failed input. Never includes the exception text: for typing it can hold the text."""
        name = type(e).__name__
        if name == "InjectPermissionError" or "INJECT_EVENTS" in str(e):
            self._input_denied.add(serial)
            raise CapabilityUnsupported(f"{what} refused by the phone", hint=INPUT_DENIED_REASON) from None
        if name == "ConnectError":
            with self._u2_lock:
                self._u2.pop(serial, None)  # reconnect (and restart the helper) on the next call
            raise AdapterError(f"{what} not sent: the phone's automation helper is not reachable") from None
        if name == "UiObjectNotFoundError":
            raise AdapterError(f"{what} not sent: no focused text field", hint="Tap a text field first") from None
        raise OutcomeUnknown(f"{what} may or may not have happened ({name})") from None

    def _u2_input(self, serial: str, what: str, fn: Callable[[Any], object]) -> None:
        try:
            fn(self._u2_device(serial))
        except Exception as e:  # noqa: BLE001 - classified below
            self._input_failed(serial, what, e)

    def _adb_shell(self, serial: str, args: list[str], what: str, timeout: float = 20) -> str:
        """Fixed command words only: adb joins these into a device shell command line."""
        try:
            r = subprocess.run([self._adb, "-s", serial, "shell", *args], capture_output=True, text=True,
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise OutcomeUnknown(f"{what} timed out after {timeout:.0f} s") from None
        except OSError as e:
            raise AdapterError(f"could not run adb: {e.strerror}", hint="Run winphone doctor") from None
        out = r.stdout + r.stderr
        if "INJECT_EVENTS" in out:
            self._input_failed(serial, what, PermissionError("INJECT_EVENTS"))
        return out

    def tap(self, serial: str, x: int, y: int) -> None:
        self._u2_input(serial, "tap", lambda d: d.click(x, y))

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._u2_input(serial, "swipe", lambda d: d.swipe(x1, y1, x2, y2, duration=duration_ms / 1000))

    def type_text(self, serial: str, text: str) -> None:
        self._u2_input(serial, "typing", lambda d: d(focused=True).set_text(text))

    def press(self, serial: str, key: Key) -> None:
        self._adb_shell(serial, ["input", "keyevent", str(KEYCODES[key])], f"press {key.value}")

    def launch_app(self, serial: str, package: str) -> None:
        if not PACKAGE_RE.fullmatch(package):
            raise InvalidRequest(f"not an Android package name: {package!r}")
        out = self._adb_shell(serial, ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                              f"launch {package}", timeout=30)
        if "No activities found" in out or "monkey aborted" in out:
            raise AdapterError(f"{package} is not installed or has no launcher activity",
                               hint="Run winphone apps to list installed packages")

    def list_apps(self, serial: str, include_system: bool) -> list[str]:
        args = ["pm", "list", "packages"] + ([] if include_system else ["-3"])
        out = self._adb_shell(serial, args, "list apps", timeout=30)
        return sorted(line.removeprefix("package:").strip() for line in out.splitlines()
                      if line.startswith("package:"))

    def logs(self, serial: str, lines: int, contains: str | None) -> list[str]:
        try:
            r = subprocess.run([self._adb, "-s", serial, "logcat", "-d", "-t", str(lines)], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=30, check=False)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"reading logs of {serial} timed out after 30 s") from None
        if r.returncode != 0:
            raise AdapterError(f"logcat failed on {serial}: {r.stderr.strip()[-200:]}")
        needle = contains.lower() if contains else None
        return [line for line in r.stdout.splitlines()
                if line.strip() and not line.startswith("---------") and (needle is None or needle in line.lower())]

    def crash_reports(self, serial: str) -> list[str]:
        raise CapabilityUnsupported(f"Android crash reports: {CRASH_REPORTS_REASON}")

    def pull_crash_report(self, serial: str, name: str, dest: Path) -> Path:
        raise CapabilityUnsupported(f"Android crash reports: {CRASH_REPORTS_REASON}")

    def release(self, serial: str) -> None:
        with self._u2_lock:
            dev = self._u2.pop(serial, None)
        if dev is not None:
            try:
                dev.stop_uiautomator()
            except Exception:  # noqa: BLE001, S110 - the phone may already be gone; nothing left to stop
                pass
