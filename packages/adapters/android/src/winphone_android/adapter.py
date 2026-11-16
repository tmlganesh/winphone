"""Android adapter: adb (via adbutils and the pinned adb.exe) and scrcpy for live view.

Docs: https://developer.android.com/tools/adb · https://github.com/Genymobile/scrcpy
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

import adbutils
from winphone_core.errors import AdapterError, DeviceNotFound, ToolMissing
from winphone_core.models import CapabilityStatus, DeviceInfo, Platform
from winphone_core.paths import app_dir
from winphone_core.tools import resolve

from winphone_android.mapping import PROP_KEYS, android_capabilities, device_info_from


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
        return android_capabilities(info.state, info.sdk, resolve("scrcpy").path is not None)

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
