from pathlib import Path

import pytest
from winphone_core import doctor
from winphone_core.doctor import acl_principals, parse_adb_devices, run_checks
from winphone_core.tools import ResolvedTool


def test_parse_adb_devices() -> None:
    out = ("* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
           "List of devices attached\nR58M\tunauthorized usb:1-1 transport_id:2\n"
           "10.0.0.5:5555\tdevice product:x model:Pixel_7\n\n")
    assert parse_adb_devices(out) == [("R58M", "unauthorized"), ("10.0.0.5:5555", "device")]


def test_acl_principals_strips_path_with_spaces() -> None:
    path = r"C:\Users\John Doe\AppData\Local\winphone\daemon.json"
    pad = " " * (len(path) + 1)
    out = (f"{path} NT AUTHORITY\\SYSTEM:(I)(F)\n{pad}BUILTIN\\Administrators:(I)(F)\n"
           f"{pad}PC\\John Doe:(I)(F)\n\nSuccessfully processed 1 files; Failed processing 0 files")
    assert acl_principals(out, path) == {r"NT AUTHORITY\SYSTEM", r"BUILTIN\Administrators", r"PC\John Doe"}


def test_doctor_without_adb_fails_with_fix(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "resolve", lambda name: ResolvedTool(name, None, "missing", None))
    checks = {c.name: c for c in run_checks()}
    assert checks["adb"].status == "fail"
    assert "winphone setup" in (checks["adb"].fix or "")
    assert checks["scrcpy"].status == "fail"
    assert checks["daemon"].status == "warn"
    assert "phones" in checks and checks["phones"].status == "fail"
    assert checks["go-ios"].status == "warn" and "winphone setup" in (checks["go-ios"].fix or "")
    assert "iphone support" in checks


def test_doctor_reports_unauthorized_phone(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_adb_devices", lambda adb: [("R58M", "unauthorized")])
    monkeypatch.setattr(doctor, "resolve", lambda name: ResolvedTool(name, Path("adb.exe"), "winphone", "1"))
    phone = next(c for c in run_checks() if c.name == "phone R58M")
    assert phone.status == "fail" and "Allow USB debugging" in (phone.fix or "")
