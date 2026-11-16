"""WindowsIOSAdapter without an iPhone: a fake usbmuxd and a replaced go-ios."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import IPHONE_UDID, closed_port, start_fake_usbmux
from winphone_core.errors import AdapterError, CapabilityUnsupported, InvalidRequest
from winphone_core.models import Capability, DeviceState, Transport
from winphone_core.tools import ResolvedTool
from winphone_ios import adapter as adapter_mod
from winphone_ios.adapter import WindowsIOSAdapter


@pytest.fixture
def mux() -> Iterator[tuple[str, int]]:
    server, address = start_fake_usbmux()
    yield address
    server.shutdown()
    server.server_close()


@pytest.fixture
def go_ios(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace go-ios: record argv, answer from a table keyed by the first argument."""
    calls: list[list[str]] = []
    answers = {
        "apps": (0, '[{"CFBundleIdentifier":"com.b.app"},{"CFBundleIdentifier":"com.a.app"}]\n', ""),
        "crash": (0, '{"files":["App-2026-11-15-120000.ips","[weird]name.ips"],"length":2}\n', ""),
        "fail": (1, "", '{"level":"WARN","msg":"agent"}\n{"level":"ERROR","msg":"Starting service failed",'
                        '"err":"instruments service unavailable"}\n'),
    }

    def run(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        code, out, err = answers.get(args[1], (0, "", ""))
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(adapter_mod.subprocess, "run", run)
    monkeypatch.setattr(adapter_mod, "resolve", lambda name: ResolvedTool(name, Path("ios.exe"), "winphone", "1.3.2"))
    return calls


def test_discover_dedupes_usb_and_network(mux: tuple[str, int]) -> None:
    found = WindowsIOSAdapter(address=mux).discover()
    assert len(found) == 1
    d = found[0]
    assert (d.id, d.model, d.os_version, d.state, d.transport) == (
        f"ios:{IPHONE_UDID}", "iPhone X", "16.7.16", DeviceState.ONLINE, Transport.USB)


def test_discover_caches_absent_service() -> None:
    a = WindowsIOSAdapter(address=closed_port())
    assert a.discover() == []
    t = time.perf_counter()
    assert a.discover() == []
    assert time.perf_counter() - t < 0.05  # remembered: no second connection attempt


def test_list_apps_parses_bundle_ids(mux: tuple[str, int], go_ios: list[list[str]]) -> None:
    a = WindowsIOSAdapter(address=mux)
    assert a.list_apps(IPHONE_UDID, include_system=False) == ["com.a.app", "com.b.app"]
    a.list_apps(IPHONE_UDID, include_system=True)
    assert go_ios[0][1:] == ["apps", f"--udid={IPHONE_UDID}"]
    assert go_ios[1][1:] == ["apps", "--all", f"--udid={IPHONE_UDID}"]


def test_go_ios_error_line_surfaces(mux: tuple[str, int], go_ios: list[list[str]]) -> None:
    with pytest.raises(AdapterError) as e:
        WindowsIOSAdapter(address=mux)._go_ios(["fail"], IPHONE_UDID)
    assert "Starting service failed" in str(e.value) and "instruments service unavailable" in str(e.value)


def test_go_ios_output_is_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real process writing UTF-8 bytes: decoded as UTF-8, not the Windows code page."""
    monkeypatch.setattr(adapter_mod, "resolve", lambda name: ResolvedTool(name, Path(sys.executable), "PATH", "x"))
    code = "import sys; sys.stdout.buffer.write('caf\\u00e9'.encode('utf-8')); sys.exit(0) #"
    out = WindowsIOSAdapter(address=closed_port())._go_ios(["-c", code], IPHONE_UDID)
    assert out.startswith("café")


def test_crash_reports_and_pull_guard(mux: tuple[str, int], go_ios: list[list[str]], tmp_path: Path) -> None:
    a = WindowsIOSAdapter(address=mux)
    assert a.crash_reports(IPHONE_UDID) == ["App-2026-11-15-120000.ips", "[weird]name.ips"]
    with pytest.raises(InvalidRequest):
        a.pull_crash_report(IPHONE_UDID, "[weird]name.ips", tmp_path)  # glob characters
    with pytest.raises(InvalidRequest):
        a.pull_crash_report(IPHONE_UDID, "../secrets.ips", tmp_path)  # not a listed name


def test_unsupported_methods_say_why(mux: tuple[str, int]) -> None:
    a = WindowsIOSAdapter(address=mux)
    with pytest.raises(CapabilityUnsupported) as e:
        a.tap(IPHONE_UDID, 1, 1)
    assert "WebDriverAgent" in str(e.value)
    with pytest.raises(CapabilityUnsupported) as e2:
        a.screenshot(IPHONE_UDID)
    assert "developer disk image" in str(e2.value)


LOG_SCRIPT = ("import json, sys, time\n"
              "for m in ['boot ok', 'MyApp started', 'other', 'MyApp tap']:\n"
              "    print(json.dumps({'msg': m}), flush=True)\n"
              "print('plain MyApp line', flush=True)\n")


def test_logs_stops_once_it_has_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real child process stands in for `ios syslog` and then keeps running: we must not wait it out."""
    monkeypatch.setattr(adapter_mod, "LOG_WINDOW_S", 30.0)
    a = WindowsIOSAdapter(address=closed_port())
    monkeypatch.setattr(a, "_syslog_cmd", lambda serial: [sys.executable, "-c", LOG_SCRIPT + "time.sleep(60)\n"])
    t = time.perf_counter()
    assert a.logs(IPHONE_UDID, lines=2, contains="myapp") == ["MyApp started", "MyApp tap"]
    assert time.perf_counter() - t < 20  # far below the 30 s window: stopped early, child killed


def test_logs_stop_when_the_stream_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_mod, "LOG_WINDOW_S", 30.0)
    a = WindowsIOSAdapter(address=closed_port())
    monkeypatch.setattr(a, "_syslog_cmd", lambda serial: [sys.executable, "-c", LOG_SCRIPT])
    t = time.perf_counter()
    assert a.logs(IPHONE_UDID, lines=50, contains="myapp") == ["MyApp started", "MyApp tap", "plain MyApp line"]
    assert time.perf_counter() - t < 20


def test_missing_go_ios_capabilities(mux: tuple[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_mod, "resolve", lambda name: ResolvedTool(name, None, "missing", None))
    caps = {c.capability: c for c in WindowsIOSAdapter(address=mux).capabilities(IPHONE_UDID)}
    assert not caps[Capability.LIST_APPS].supported and "winphone setup" in (caps[Capability.LIST_APPS].reason or "")
