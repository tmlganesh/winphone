"""Pinned, checksum-verified external tools (adb, scrcpy), installed per user.

Neither tool is bundled: Android Platform-Tools may not be redistributed, so `winphone setup`
downloads them on the user's machine.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from winphone_core.errors import ChecksumMismatch
from winphone_core.paths import app_dir


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    url: str
    sha256: str
    exe: str  # path of the executable inside the archive


# Pins recorded 2026-11-16.
# platform-tools: SHA-1 b3fde523...4172 checked against Google's repository2-3.xml before pinning.
# scrcpy: SHA-256 from the GitHub release asset digest.
TOOLS: dict[str, ToolSpec] = {
    "adb": ToolSpec(
        name="adb",
        version="37.0.3",
        url="https://dl.google.com/android/repository/platform-tools_r37.0.3-win.zip",
        sha256="b5d2015f5d5b7a24a2ac646a245f20f63fb501e4174c61863c74bed1df6de4bd",
        exe="platform-tools/adb.exe",
    ),
    "scrcpy": ToolSpec(
        name="scrcpy",
        version="5.0.1",
        url="https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-win64-v5.0.1.zip",
        sha256="b12a2c4ee8be317422451fc7dcf8ee20a71b5ea7ef9ad73ddd825a25316227a5",
        exe="scrcpy-win64-v5.0.1/scrcpy.exe",
    ),
}


@dataclass(frozen=True)
class ResolvedTool:
    name: str
    path: Path | None
    source: Literal["winphone", "PATH", "missing"]
    version: str | None


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "winphone-setup"})
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 - pinned https URLs only
        data: bytes = resp.read()
    return data


def _install_dir(spec: ToolSpec) -> Path:
    return app_dir() / "tools" / f"{spec.name}-{spec.version}"


def install(spec: ToolSpec, *, fetch: Callable[[str], bytes] = http_get, force: bool = False) -> Path:
    target = _install_dir(spec)
    exe = target / spec.exe
    if exe.exists() and not force:
        return exe
    data = fetch(spec.url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != spec.sha256:
        raise ChecksumMismatch(
            f"{spec.name} {spec.version} download does not match its pinned checksum",
            hint="Nothing was installed. Retry; if it persists the download source changed.",
        )
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            p = PurePosixPath(member.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"refusing archive member outside the install folder: {member}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{spec.name}-", dir=target.parent))
        try:
            z.extractall(staging)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return exe


_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _probe_version(name: str, path: Path) -> str | None:
    # adb prints "Version 37.0.3-..." on its second line; scrcpy prints "scrcpy 5.0.1 <url>".
    args = [str(path), "version"] if name == "adb" else [str(path), "--version"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if name == "adb" and not line.startswith("Version"):
            continue
        m = _VERSION.search(line)
        if m:
            return m.group(1)
    return None


# Oldest version accepted from PATH: adb 30 added `adb pair`; scrcpy 2.0 is the oldest we test flags against.
MIN_VERSIONS: dict[str, tuple[int, ...]] = {"adb": (30, 0), "scrcpy": (2, 0)}


def _supported(name: str, version: str | None) -> bool:
    return version is not None and tuple(int(p) for p in version.split(".")) >= MIN_VERSIONS[name]


def resolve(name: str) -> ResolvedTool:
    """The winphone-managed copy first; a PATH copy only if its version is supported. A too-old PATH
    copy comes back as missing, with the version that was found."""
    spec = TOOLS[name]
    managed = _install_dir(spec) / spec.exe
    if managed.exists():
        return ResolvedTool(name, managed, "winphone", spec.version)
    found = shutil.which(name)
    if found:
        version = _probe_version(name, Path(found))
        if _supported(name, version):
            return ResolvedTool(name, Path(found), "PATH", version)
        return ResolvedTool(name, None, "missing", version)
    return ResolvedTool(name, None, "missing", None)
