import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import pytest
from winphone_core import tools
from winphone_core.errors import ChecksumMismatch
from winphone_core.tools import ToolSpec, install, resolve


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


ZIP = _zip({"platform-tools/adb.exe": b"exe", "platform-tools/AdbWinApi.dll": b"dll"})
EVIL = _zip({"platform-tools/adb.exe": b"exe", "../../../x": b"pwned"})


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec(sha256: str) -> ToolSpec:
    return ToolSpec(name="adb", version="1.0", url="https://example.invalid/a.zip", sha256=sha256,
                    exe="platform-tools/adb.exe")


def test_install_refuses_checksum_mismatch(tmp_home: Path) -> None:
    with pytest.raises(ChecksumMismatch):
        install(spec("0" * 64), fetch=lambda u: ZIP)
    tools_dir = tmp_home / "tools"
    assert not tools_dir.exists() or not any(tools_dir.iterdir())


def test_install_extracts_and_returns_exe(tmp_home: Path) -> None:
    exe = install(spec(sha(ZIP)), fetch=lambda u: ZIP)
    assert exe.read_bytes() == b"exe"
    assert exe.parent.joinpath("AdbWinApi.dll").exists()


def test_install_rejects_zip_slip(tmp_home: Path) -> None:
    with pytest.raises(ValueError):
        install(spec(sha(EVIL)), fetch=lambda u: EVIL)
    assert not (tmp_home.parent / "x").exists()
    assert not (tmp_home / "tools" / "adb-1.0").exists()


def test_install_is_skipped_when_present_unless_forced(tmp_home: Path) -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return ZIP

    install(spec(sha(ZIP)), fetch=fetch)
    install(spec(sha(ZIP)), fetch=fetch)
    install(spec(sha(ZIP)), fetch=fetch, force=True)
    assert len(calls) == 2


def test_resolve_prefers_winphone_managed(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tools.TOOLS, "adb", spec(sha(ZIP)))
    install(tools.TOOLS["adb"], fetch=lambda u: ZIP)
    r = resolve("adb")
    assert r.source == "winphone" and r.version == "1.0" and r.path is not None and r.path.exists()


def test_resolve_refuses_too_old_tool_on_path(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review finding 4: an adb on PATH without `adb pair` (before 30.0) was used silently."""
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\old\adb.exe")
    monkeypatch.setattr(tools, "_probe_version", lambda name, path: "29.0.6")
    r = resolve("adb")
    assert r.source == "missing" and r.path is None and r.version == "29.0.6"


def test_resolve_accepts_supported_tool_on_path(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\sdk\adb.exe")
    monkeypatch.setattr(tools, "_probe_version", lambda name, path: "35.0.2")
    r = resolve("adb")
    assert r.source == "PATH" and r.version == "35.0.2"


def test_resolve_reports_missing(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = resolve("scrcpy")
    assert r.source == "missing" and r.path is None
