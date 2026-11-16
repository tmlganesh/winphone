"""`winphone doctor`: what works, what doesn't, and the one-line fix for each. Never starts the daemon."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from winphone_core.client import connect_daemon
from winphone_core.errors import WinphoneError
from winphone_core.runtime import info_path, read_info
from winphone_core.tools import resolve

Status = Literal["ok", "warn", "fail"]
_USB_HINT = "Unlock the phone and accept 'Allow USB debugging' (tick 'Always allow')"
_VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")


class Check(BaseModel):
    name: str
    status: Status
    detail: str
    fix: str | None = None


def _run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def parse_adb_devices(out: str) -> list[tuple[str, str]]:
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(("*", "List of devices")):
            rows.append((parts[0], parts[1]))
    return rows


def acl_principals(icacls_out: str, path: str) -> set[str]:
    found = set()
    for line in icacls_out.splitlines():
        entry = line.removeprefix(path).strip()
        if ":(" in entry:
            found.add(entry.split(":(", 1)[0])
    return found


def _adb_devices(adb: Path) -> list[tuple[str, str]]:
    return parse_adb_devices(_run([str(adb), "devices"]).stdout)


def _tool(name: str) -> Check:
    t = resolve(name)
    if t.path is None:
        detail = f"only {t.version} found on PATH, too old" if t.version else "not installed"
        return Check(name=name, status="fail", detail=detail, fix="Run winphone setup")
    return Check(name=name, status="ok", detail=f"{t.version or 'unknown version'} ({t.source}) {t.path}")


def _daemon() -> list[Check]:
    try:
        version = connect_daemon(autostart=False, timeout=2).healthz()
    except WinphoneError:
        return [Check(name="daemon", status="warn", detail="not running (starts on first use)")]
    info = read_info()
    checks = [Check(name="daemon", status="ok",
                    detail=f"winphoned {version}, pid {info.pid if info else '?'}")]
    path = str(info_path())
    allowed = {r"NT AUTHORITY\SYSTEM", r"BUILTIN\Administrators", "OWNER RIGHTS",
               f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"}
    extra = acl_principals(_run(["icacls", path]).stdout, path) - allowed
    if extra:
        checks.append(Check(name="daemon token file", status="warn",
                            detail=f"also readable by: {', '.join(sorted(extra))}",
                            fix=f'icacls "{path}" /inheritance:r /grant:r %USERNAME%:F'))
    else:
        checks.append(Check(name="daemon token file", status="ok", detail="readable by you only"))
    return checks


def _phones() -> list[Check]:
    adb = resolve("adb")
    if adb.path is None:
        return [Check(name="phones", status="fail", detail="cannot list phones without adb",
                      fix="Run winphone setup")]
    rows = _adb_devices(adb.path)
    if not rows:
        return [Check(name="phones", status="warn", detail="no phone found",
                      fix="Enable Developer options > USB debugging and use a data cable")]
    fixes = {"unauthorized": _USB_HINT, "offline": "Replug the cable or toggle USB debugging off and on"}
    return [Check(name=f"phone {serial}", status="ok" if state == "device" else "fail",
                  detail="online" if state == "device" else state, fix=fixes.get(state))
            for serial, state in rows]


def _later_phases() -> list[Check]:
    rustc = shutil.which("rustc")
    msvc = _VSWHERE.exists() and bool(_run([str(_VSWHERE), "-products", "*", "-requires",
                                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                            "-property", "installationPath"]).stdout.strip())
    apple = "RUNNING" in _run(["sc", "query", "Apple Mobile Device Service"]).stdout
    return [
        Check(name="rust (phase 4)", status="ok" if rustc else "warn",
              detail=_run([rustc, "--version"]).stdout.strip() if rustc else "not installed",
              fix=None if rustc else "Install rustup from https://rustup.rs"),
        Check(name="msvc c++ tools (phase 4)", status="ok" if msvc else "warn",
              detail="installed" if msvc else "not installed",
              fix=None if msvc else "Visual Studio Installer > 'Desktop development with C++'"),
        Check(name="apple mobile device service (phase 5)", status="ok" if apple else "warn",
              detail="running" if apple else "not running or not installed",
              fix=None if apple else "Install Apple Devices from the Microsoft Store"),
    ]


def run_checks() -> list[Check]:
    py = sys.version_info
    return [
        Check(name="python", status="ok" if py >= (3, 11) else "fail", detail=sys.version.split()[0],
              fix=None if py >= (3, 11) else "Use Python 3.11 or newer"),
        _tool("adb"),
        _tool("scrcpy"),
        *_daemon(),
        *_phones(),
        *_later_phases(),
    ]
