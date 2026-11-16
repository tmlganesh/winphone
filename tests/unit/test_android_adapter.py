"""AndroidAdapter behaviour that needs no phone: its adb client and subprocess calls are replaced."""

from __future__ import annotations

import subprocess
import threading
import traceback
from dataclasses import dataclass

import adbutils
import pytest
from winphone_android import adapter as adapter_mod
from winphone_android.adapter import AndroidAdapter
from winphone_core.errors import AdapterError


@dataclass
class Listed:
    serial: str
    state: str


class FakeDevice:
    def __init__(self, serial: str, fail: bool) -> None:
        self.serial, self.fail = serial, fail

    def shell(self, cmd: str, timeout: float) -> str:
        if self.fail:
            raise adbutils.AdbTimeout("getprop timed out")
        return "Pixel 7\n14\n34"


class FakeAdbClient:
    def __init__(self, listed: list[Listed], failing: set[str]) -> None:
        self.listed, self.failing = listed, failing

    def list(self) -> list[Listed]:
        return self.listed

    def device(self, serial: str) -> FakeDevice:
        return FakeDevice(serial, serial in self.failing)


def bare_adapter(client: FakeAdbClient | None = None) -> AndroidAdapter:
    a = AndroidAdapter.__new__(AndroidAdapter)  # skip __init__: no adb server is started
    a._adb = "adb.exe"
    a._client = client
    a._props = {}
    a._props_lock = threading.Lock()
    return a


def test_one_phone_failing_getprop_does_not_hide_the_others() -> None:
    """Review finding 5: one slow phone made discover() fail for every phone."""
    a = bare_adapter(FakeAdbClient([Listed("A", "device"), Listed("B", "device")], failing={"B"}))
    found = {d.serial: d for d in a.discover()}
    assert found["A"].model == "Pixel 7"
    assert found["B"].model is None and "B" in found


def test_pair_timeout_hides_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review finding 2: TimeoutExpired carried argv (with the code) into a 500 and the daemon log."""
    def boom(args: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(adapter_mod.subprocess, "run", boom)
    code = str(400000 + 23456)  # built at runtime so the traceback's own source lines cannot contain it
    with pytest.raises(AdapterError) as e:
        bare_adapter().pair("10.0.0.5", 37123, code)
    shown = "".join(traceback.format_exception(e.value))  # what a log or console would print
    assert code not in f"{shown} {e.value.hint}"


def test_screenshot_timeout_is_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(args: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(adapter_mod.subprocess, "run", boom)
    with pytest.raises(AdapterError):
        bare_adapter().screenshot("A")
