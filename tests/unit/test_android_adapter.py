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
from winphone_core.errors import AdapterError, CapabilityUnsupported, InvalidRequest, OutcomeUnknown
from winphone_core.models import Capability


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
        if "mCurrentFocus" in cmd:
            return "  mCurrentFocus=Window{1 u0 com.android.settings/com.android.settings.Settings}"
        return "Pixel 7\n14\n34"


class FakeU2:
    """Stands in for a uiautomator2.Device."""

    def __init__(self, dump_error: Exception | None = None, input_error: Exception | None = None) -> None:
        self.dump_error, self.input_error = dump_error, input_error
        self.calls: list[tuple[object, ...]] = []

    def dump_hierarchy(self) -> str:
        if self.dump_error:
            raise self.dump_error
        return '<hierarchy rotation="0"><node text="from-u2" bounds="[0,0][10,10]"/></hierarchy>'

    def click(self, x: int, y: int) -> None:
        self.calls.append(("click", x, y))
        if self.input_error:
            raise self.input_error

    def __call__(self, **selector: object) -> FakeU2:
        return self

    def set_text(self, text: str) -> None:
        if self.input_error:
            raise self.input_error
        self.calls.append(("set_text", len(text)))

    def stop_uiautomator(self) -> None:
        self.calls.append(("stop",))


class FakeAdbClient:
    def __init__(self, listed: list[Listed], failing: set[str]) -> None:
        self.listed, self.failing = listed, failing

    def list(self) -> list[Listed]:
        return self.listed

    def device(self, serial: str) -> FakeDevice:
        return FakeDevice(serial, serial in self.failing)


def bare_adapter(client: FakeAdbClient | None = None, u2dev: FakeU2 | None = None) -> AndroidAdapter:
    a = AndroidAdapter.__new__(AndroidAdapter)  # skip __init__: no adb server is started
    a._adb = "adb.exe"
    a._client = client
    a._props = {}
    a._props_lock = threading.Lock()
    a._u2 = {"A": u2dev} if u2dev else {}
    a._u2_lock = threading.Lock()
    a._sizes = {"A": (1224, 2720)}
    a._input_denied = set()
    return a


def test_snapshot_uses_u2_and_reads_foreground_app() -> None:
    a = bare_adapter(FakeAdbClient([Listed("A", "device")], set()), FakeU2())
    snap = a.ui_snapshot("A")
    assert "from-u2" in snap.xml
    assert (snap.foreground_app, snap.width, snap.height) == ("com.android.settings", 1224, 2720)


def test_snapshot_falls_back_to_builtin_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    builtin = ("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
               '<node text="from-builtin" bounds="[0,0][10,10]"/></hierarchy>UI hierchary dumped to: /dev/tty')

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[-3:] == ["uiautomator", "dump", "/dev/tty"]
        return subprocess.CompletedProcess(args, 0, builtin, "")

    monkeypatch.setattr(adapter_mod.subprocess, "run", run)
    a = bare_adapter(FakeAdbClient([Listed("A", "device")], set()), FakeU2(dump_error=RuntimeError("u2 down")))
    snap = a.ui_snapshot("A")
    assert "from-builtin" in snap.xml and snap.xml.rstrip().endswith("</hierarchy>")


def test_inject_events_denial_marks_input_unsupported() -> None:
    denied = RuntimeError("java.lang.SecurityException: Injecting input events requires INJECT_EVENTS")
    a = bare_adapter(FakeAdbClient([Listed("A", "device")], set()), FakeU2(input_error=denied))
    with pytest.raises(CapabilityUnsupported):
        a.tap("A", 10, 10)
    caps = {c.capability: c for c in a.capabilities("A")}
    assert not caps[Capability.TAP].supported


def test_unexpected_input_error_is_outcome_unknown() -> None:
    a = bare_adapter(FakeAdbClient([Listed("A", "device")], set()), FakeU2(input_error=RuntimeError("timeout")))
    with pytest.raises(OutcomeUnknown):
        a.tap("A", 10, 10)


def test_type_text_error_never_contains_the_text() -> None:
    secret = "hunter" + "2"
    leak = RuntimeError(f"RPC setText params ['{secret}'] failed")
    a = bare_adapter(FakeAdbClient([Listed("A", "device")], set()), FakeU2(input_error=leak))
    with pytest.raises(OutcomeUnknown) as e:
        a.type_text("A", secret)
    assert secret not in "".join(traceback.format_exception(e.value)) + str(e.value.hint)


def test_launch_rejects_bad_package() -> None:
    for bad in ("-x", "a;b", "com.x\n", "com.x\nreboot"):
        with pytest.raises(InvalidRequest):
            bare_adapter().launch_app("A", bad)


def test_release_stops_the_helper() -> None:
    dev = FakeU2()
    a = bare_adapter(u2dev=dev)
    a.release("A")
    assert ("stop",) in dev.calls and "A" not in a._u2
    a.release("A")  # second release is a no-op


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
