import os
import subprocess
import sys
import time
from pathlib import Path

from winphone_core.doctor import acl_principals
from winphone_core.runtime import (
    DaemonInfo,
    acquire_lock,
    info_path,
    read_info,
    should_exit,
    spawn_detached,
    write_info,
)


def test_should_exit_only_when_idle_and_nothing_held() -> None:
    assert should_exit(2000, 100, 0)
    assert not should_exit(2000, 100, 1)
    assert not should_exit(500, 100, 0)


def test_write_read_info_roundtrip(tmp_home: Path) -> None:
    info = DaemonInfo(port=4555, token="t", pid=1, version="0.1.0")
    write_info(info)
    assert read_info() == info


def test_info_file_is_readable_by_current_user_only(tmp_home: Path) -> None:
    write_info(DaemonInfo(port=4555, token="t", pid=1, version="0.1.0"))
    path = str(info_path())
    out = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=15, check=True).stdout
    me = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    principals = acl_principals(out, path)
    # SYSTEM and Administrators can read any user's files anyway; nothing else may remain.
    assert me in principals
    assert principals <= {me, r"NT AUTHORITY\SYSTEM", r"BUILTIN\Administrators", "OWNER RIGHTS"}


def test_spawned_daemon_has_no_visible_console(tmp_path: Path) -> None:
    """Review finding 1: the venv launcher started with DETACHED_PROCESS gave its child a visible console."""
    out = tmp_path / "console.txt"
    code = ("import ctypes; k = ctypes.windll.kernel32; u = ctypes.windll.user32; h = k.GetConsoleWindow(); "
            f"open(r'{out}', 'w').write(f'{{h}} {{u.IsWindowVisible(h) if h else 0}}')")
    spawn_detached([sys.executable, "-c", code])
    for _ in range(100):
        if out.exists() and out.read_text():
            break
        time.sleep(0.1)
    _, visible = out.read_text().split()
    assert visible == "0"


def test_read_info_missing_or_corrupt_is_none(tmp_home: Path) -> None:
    assert read_info() is None
    (tmp_home / "daemon.json").write_text("{not json")
    assert read_info() is None


def test_second_lock_is_refused(tmp_home: Path) -> None:
    first = acquire_lock()
    assert first is not None
    try:
        assert acquire_lock() is None
    finally:
        first.close()
