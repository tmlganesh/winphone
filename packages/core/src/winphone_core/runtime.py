"""Daemon lifecycle: one per Windows user, discoverable through daemon.json, gone after 30 idle minutes."""

from __future__ import annotations

import msvcrt
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from winphone_core import __version__
from winphone_core.paths import app_dir

if TYPE_CHECKING:
    from winphone_core.orchestrator import Orchestrator

IDLE_LIMIT_S = 1800.0


class DaemonInfo(BaseModel):
    port: int
    token: str
    pid: int
    version: str


def info_path() -> Path:
    return app_dir() / "daemon.json"


def write_info(info: DaemonInfo) -> None:
    """The file holds the daemon token: strip inherited ACEs (LocalAppData can carry AppContainer
    grants) so only the current user can read it. The rename keeps the file's own ACL."""
    tmp = info_path().with_suffix(".tmp")
    tmp.write_text(info.model_dump_json(), encoding="utf-8")
    me = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    subprocess.run(["icacls", str(tmp), "/inheritance:r", "/grant:r", f"{me}:F"],
                   capture_output=True, timeout=15, check=True)
    os.replace(tmp, info_path())


def read_info() -> DaemonInfo | None:
    try:
        return DaemonInfo.model_validate_json(info_path().read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def acquire_lock() -> IO[bytes] | None:
    """Hold for the daemon's whole life. None means another daemon already holds it."""
    f = open(app_dir() / "daemon.lock", "a+b")  # noqa: SIM115 - must stay open while the daemon runs
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def should_exit(now: float, last_request: float, held: int, idle_limit: float = IDLE_LIMIT_S) -> bool:
    return held == 0 and now - last_request >= idle_limit


def serve(orch: Orchestrator, idle_limit: float = IDLE_LIMIT_S) -> None:
    # Imported here so clients (CLI, MCP) never pay for FastAPI and uvicorn.
    import uvicorn

    from winphone_core.server import create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)

    def stop_server() -> None:
        server.should_exit = True

    app = create_app(orch, token, port, on_shutdown=stop_server)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    stop = threading.Event()

    def watch_idle() -> None:
        while not stop.wait(60):
            if should_exit(time.monotonic(), app.state.last_request, orch.held_count(), idle_limit):
                stop_server()
                return

    write_info(DaemonInfo(port=port, token=token, pid=os.getpid(), version=__version__))
    threading.Thread(target=watch_idle, name="idle-watch", daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        stop.set()
        current = read_info()
        if current is not None and current.pid == os.getpid():
            info_path().unlink(missing_ok=True)


def _popen_detached(cmd: list[str], flags: int) -> None:
    subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def spawn_detached(cmd: list[str]) -> None:
    """Start the daemon so it outlives the CLI, the MCP server and Claude Code, with no window.
    CREATE_NO_WINDOW, not DETACHED_PROCESS: the venv's python.exe is a launcher, and a detached launcher
    hands the real interpreter a new, visible console (closing it would kill the daemon)."""
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        _popen_detached(cmd, flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:  # the parent's job object forbids breakaway
        _popen_detached(cmd, flags)
