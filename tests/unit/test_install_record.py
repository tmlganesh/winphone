from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fakes import FakeAdapter, dev, permissions_for
from fastapi.testclient import TestClient
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import InvalidRequest
from winphone_core.orchestrator import Orchestrator
from winphone_core.server import create_app

A, PORT = "android:A", 4571
H = {"Authorization": "Bearer t", "X-Winphone-Client": "human"}


def app_for(tmp_path: Path, answer: bool) -> tuple[TestClient, FakeAdapter]:
    fake = FakeAdapter([dev("A")])
    app = create_app(Orchestrator([fake], ArtifactStore(tmp_path / "art")), "t", PORT, lambda: None,
                     permissions_for(tmp_path, answer=answer))
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}"), fake


def apk(tmp_path: Path) -> Path:
    p = tmp_path / "MyApp.apk"
    p.write_bytes(b"PK")
    return p


def test_install_needs_a_click_even_for_a_human(tmp_path: Path) -> None:
    c, fake = app_for(tmp_path, answer=False)
    r = c.post(f"/devices/{A}/install", headers=H, json={"apk": str(apk(tmp_path))})
    assert r.status_code == 409 and r.json()["error"] == "permission_denied" and fake.calls == []
    c2, fake2 = app_for(tmp_path, answer=True)
    assert c2.post(f"/devices/{A}/install", headers=H, json={"apk": str(apk(tmp_path))}).status_code == 200
    assert fake2.calls == [("install", "MyApp.apk")]


def test_install_on_unsupported_phone_never_asks_the_human(tmp_path: Path) -> None:
    from winphone_core.audit import AuditLog
    from winphone_core.models import Capability
    from winphone_core.permissions import PermissionManager, SensitivityPolicy

    prompts: list[str] = []
    perms = PermissionManager(confirm=lambda m: prompts.append(m) is None, audit=AuditLog(tmp_path / "a.jsonl"),
                              policy=SensitivityPolicy.default())
    fake = FakeAdapter([dev("A")], unsupported={Capability.INSTALL_APP: "not on this phone"})
    app = create_app(Orchestrator([fake], ArtifactStore(tmp_path / "art")), "t", PORT, lambda: None, perms)
    r = TestClient(app, base_url=f"http://127.0.0.1:{PORT}").post(f"/devices/{A}/install", headers=H,
                                                                  json={"apk": str(apk(tmp_path))})
    assert r.json()["error"] == "capability_unsupported" and prompts == []


def test_install_rejects_missing_or_non_apk(tmp_path: Path) -> None:
    o = Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path))
    (tmp_path / "notes.txt").write_text("x")
    for bad in (str(tmp_path / "missing.apk"), str(tmp_path / "notes.txt")):
        with pytest.raises(InvalidRequest):
            o.install_app(A, bad)


def test_record_bounds_and_artifact(tmp_path: Path) -> None:
    o = Orchestrator([FakeAdapter([dev("A")])], ArtifactStore(tmp_path))
    for bad in (0, 61):
        with pytest.raises(InvalidRequest):
            o.record(A, bad)
    path = Path(o.record(A, 5))
    assert path.exists() and tmp_path in path.parents and "recordings" in path.parts


def test_android_install_and_record_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from winphone_android import adapter as adapter_mod
    from winphone_android.adapter import AndroidAdapter
    from winphone_core.errors import AdapterError
    from winphone_core.tools import ResolvedTool

    calls: list[list[str]] = []
    outputs = iter([(0, "Performing Streamed Install\nSuccess\n"),
                    (1, "adb: failed to install x.apk: Failure [INSTALL_FAILED_VERSION_DOWNGRADE]\n")])

    def run(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "--record" in " ".join(args):
            out = next(a for a in args if a.startswith("--record=")).split("=", 1)[1]
            Path(out).write_bytes(b"mp4")
            return subprocess.CompletedProcess(args, 0, "", "")
        code, text = next(outputs)
        return subprocess.CompletedProcess(args, code, text, "")

    monkeypatch.setattr(adapter_mod.subprocess, "run", run)
    monkeypatch.setattr(adapter_mod, "resolve", lambda n: ResolvedTool(n, Path("scrcpy.exe"), "winphone", "5"))
    a = AndroidAdapter.__new__(AndroidAdapter)
    a._adb = "adb.exe"
    a.install_app("A", apk(tmp_path))
    assert calls[0][-3:] == ["install", "-r", str(apk(tmp_path))]
    with pytest.raises(AdapterError) as e:
        a.install_app("A", apk(tmp_path))
    assert "INSTALL_FAILED_VERSION_DOWNGRADE" in str(e.value)
    path = a.record("A", 5, tmp_path / "rec")
    assert path.exists() and {"--no-window", "--no-audio", "--no-control", "--time-limit=5"} <= set(calls[-1])
