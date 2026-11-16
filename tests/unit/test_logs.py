from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeAdapter, dev
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import CapabilityUnsupported, InvalidRequest
from winphone_core.models import Capability
from winphone_core.orchestrator import Orchestrator

A = "android:A"


def orch(tmp_path: Path, **kw: object) -> Orchestrator:
    return Orchestrator([FakeAdapter([dev("A")], **kw)], ArtifactStore(tmp_path))  # type: ignore[arg-type]


def test_logs_filtered(tmp_path: Path) -> None:
    assert orch(tmp_path).logs(A, lines=10, contains="myapp") == ["MyApp started", "MyApp tap"]


def test_logs_capability_checked(tmp_path: Path) -> None:
    with pytest.raises(CapabilityUnsupported):
        orch(tmp_path, unsupported={Capability.LOGS: "no"}).logs(A)


def test_logs_lines_bounded(tmp_path: Path) -> None:
    for bad in (0, 2001):
        with pytest.raises(InvalidRequest):
            orch(tmp_path).logs(A, lines=bad)


def test_crash_reports_listed(tmp_path: Path) -> None:
    assert orch(tmp_path).crash_reports(A) == ["MyApp-2026-11-15.ips"]


def test_pull_saves_into_artifacts(tmp_path: Path) -> None:
    path = Path(orch(tmp_path).pull_crash_report(A, "MyApp-2026-11-15.ips"))
    assert path.read_text() == "crash" and tmp_path in path.parents and "crashes" in path.parts


def test_pull_rejects_unlisted_name(tmp_path: Path) -> None:
    for bad in ("../x.ips", "other.ips", "C:\\Windows\\win.ini"):
        with pytest.raises(InvalidRequest):
            orch(tmp_path).pull_crash_report(A, bad)


def test_android_logcat_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from winphone_android import adapter as adapter_mod
    from winphone_android.adapter import AndroidAdapter

    def run(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        assert args[-4:] == ["logcat", "-d", "-t", "50"]
        return subprocess.CompletedProcess(args, 0, "I boot\nI MyApp: start\nW MyApp: slow\n", "")

    monkeypatch.setattr(adapter_mod.subprocess, "run", run)
    a = AndroidAdapter.__new__(AndroidAdapter)
    a._adb = "adb.exe"
    assert a.logs("A", lines=50, contains="myapp") == ["I MyApp: start", "W MyApp: slow"]
    with pytest.raises(CapabilityUnsupported):
        a.crash_reports("A")
