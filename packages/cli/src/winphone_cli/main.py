"""winphone CLI. Device commands go through the winphoned daemon (started on demand);
setup, doctor and the daemon commands work without it."""

from __future__ import annotations

import functools
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar

import typer
from winphone_core.client import DaemonClient, connect_daemon
from winphone_core.errors import DaemonUnavailable, InvalidRequest, WinphoneError
from winphone_core.models import NOT_READY_HINTS, DeviceInfo

from winphone_cli import output

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Drive real phones from Windows.")
daemon_app = typer.Typer(no_args_is_help=True, help="Control the winphoned background service.")
app.add_typer(daemon_app, name="daemon")

P = ParamSpec("P")
R = TypeVar("R")

DeviceOpt = Annotated[str | None, typer.Option("--device", "-d", help="Device id or serial.")]


@dataclass
class Opts:
    json: bool = False
    timeout: float = 60.0


def _opts(ctx: typer.Context) -> Opts:
    root = ctx.find_root()
    return root.obj if isinstance(root.obj, Opts) else Opts()


def handled(f: Callable[P, R]) -> Callable[P, R]:
    """Turn WinphoneError into its message and exit code instead of a traceback.
    Every command takes `ctx` as its first parameter."""

    @functools.wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return f(*args, **kwargs)
        except WinphoneError as e:
            # At runtime ctx is Typer's vendored click Context, not typer.Context: duck-type it.
            ctx = kwargs.get("ctx", args[0] if args else None)
            output.error(e, _opts(ctx).json if hasattr(ctx, "find_root") else False)  # type: ignore[arg-type]
            raise typer.Exit(e.exit_code) from e

    return wrapper


@app.callback()
def main(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds to wait for each daemon call.")] = 60.0,
) -> None:
    ctx.obj = Opts(json=json_out, timeout=timeout)


def _client(ctx: typer.Context) -> DaemonClient:
    return connect_daemon(call_timeout=_opts(ctx).timeout)


def _host_port(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise InvalidRequest(f"expected HOST:PORT, got {value!r}")
    return host, int(port)


def _show(ctx: typer.Context, data: Any, human: Callable[[], None]) -> None:
    if _opts(ctx).json:
        output.emit_json(data)
    else:
        human()


def _device_rows(devices: list[DeviceInfo]) -> list[list[Any]]:
    return [[d.id, d.state.value, d.transport.value, d.model, d.os_version, "yes" if d.held else ""]
            for d in devices]


@app.command()
@handled
def setup(ctx: typer.Context,
          force: Annotated[bool, typer.Option(help="Download again even if installed.")] = False) -> None:
    """Download the pinned adb and scrcpy, verifying their checksums."""
    from winphone_core.tools import TOOLS, install

    results = []
    for spec in TOOLS.values():
        if not _opts(ctx).json:
            output.say(f"{spec.name} {spec.version}: installing")
        results.append({"name": spec.name, "version": spec.version, "path": str(install(spec, force=force))})
    _show(ctx, results, lambda: output.table(["TOOL", "VERSION", "PATH"],
                                             [[r["name"], r["version"], r["path"]] for r in results]))


@app.command()
@handled
def doctor(ctx: typer.Context) -> None:
    """Check tools, the daemon and connected phones; exit 1 if anything required is broken."""
    from winphone_core.doctor import run_checks

    checks = run_checks()
    marks = {"ok": "[green]ok[/green]", "warn": "[yellow]warn[/yellow]", "fail": "[red]FAIL[/red]"}
    _show(ctx, checks, lambda: output.table(["", "CHECK", "DETAIL", "FIX"],
                                            [[marks[c.status], c.name, c.detail, c.fix] for c in checks]))
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)


@app.command()
@handled
def devices(ctx: typer.Context) -> None:
    """List phones adb can see."""
    found = _client(ctx).devices()

    def human() -> None:
        if not found:
            output.say("No phones found. Run winphone doctor.")
            return
        output.table(["ID", "STATE", "TRANSPORT", "MODEL", "OS", "HELD"], _device_rows(found))
        for d in found:
            if d.state.value in NOT_READY_HINTS:
                output.say(f"hint: {d.id}: {NOT_READY_HINTS[d.state.value]}")

    _show(ctx, found, human)


@app.command()
@handled
def connect(
    ctx: typer.Context,
    device: DeviceOpt = None,
    wifi: Annotated[str | None, typer.Option(help="Connect over Wi-Fi to HOST:PORT.")] = None,
    pair: Annotated[str | None, typer.Option(help="Pair first with HOST:PORT (Android 11+).")] = None,
    code: Annotated[str | None, typer.Option(help="6-digit pairing code shown on the phone.")] = None,
) -> None:
    """Hold a phone for this session; with --wifi/--pair, link it over the network first."""
    pair_addr = _host_port(pair) if pair else None
    wifi_addr = _host_port(wifi) if wifi else None
    if pair_addr and not code:
        raise InvalidRequest("--pair needs --code")
    client = _client(ctx)
    if pair_addr and code:
        client.pair_wifi(*pair_addr, code)
        if not _opts(ctx).json:
            output.say(f"Paired with {pair}.")
        if not wifi_addr:
            return
    target = client.connect_wifi(*wifi_addr).id if wifi_addr else client.resolve(device).id
    d = client.connect(target)
    summary = f"Connected to {d.id} ({d.model or 'unknown model'}, {d.transport.value})."
    _show(ctx, d, lambda: output.say(summary))


@app.command()
@handled
def disconnect(ctx: typer.Context, device: DeviceOpt = None) -> None:
    """Release a phone (and drop its Wi-Fi link)."""
    client = _client(ctx)
    d = client.resolve(device)
    client.disconnect(d.id)
    _show(ctx, {"id": d.id, "disconnected": True}, lambda: output.say(f"Disconnected {d.id}."))


@app.command()
@handled
def capabilities(ctx: typer.Context, device: DeviceOpt = None) -> None:
    """Show what this phone can do right now, as probed, with the reason for anything missing."""
    client = _client(ctx)
    caps = client.capabilities(client.resolve(device).id)
    _show(ctx, caps, lambda: output.table(
        ["CAPABILITY", "SUPPORTED", "REASON"],
        [[c.capability.value, "yes" if c.supported else "no", c.reason] for c in caps]))


@app.command()
@handled
def screenshot(
    ctx: typer.Context,
    device: DeviceOpt = None,
    out: Annotated[Path | None, typer.Option("--output", "-o", help="Also copy the PNG here.")] = None,
) -> None:
    """Capture the screen as a PNG (kept under the WINPHONE artifacts folder)."""
    client = _client(ctx)
    art = client.screenshot(client.resolve(device).id)
    if out is not None:
        shutil.copyfile(art.path, out)
        art = art.model_copy(update={"path": str(out)})
    _show(ctx, art, lambda: output.say(f"{art.path}  {art.width}x{art.height}"))


@app.command()
@handled
def view(ctx: typer.Context, device: DeviceOpt = None) -> None:
    """Open the live screen in a scrcpy window (mouse and keyboard work there)."""
    client = _client(ctx)
    d = client.resolve(device)
    pid = client.view(d.id)
    _show(ctx, {"id": d.id, "pid": pid}, lambda: output.say(f"Live view of {d.id} opened (pid {pid})."))


@daemon_app.command("run")
@handled
def daemon_run(ctx: typer.Context) -> None:
    """Run winphoned in the foreground (this is what auto-start launches)."""
    from winphone_android.adapter import AndroidAdapter
    from winphone_core.artifacts import ArtifactStore
    from winphone_core.orchestrator import Orchestrator
    from winphone_core.paths import app_dir
    from winphone_core.runtime import acquire_lock, read_info, serve

    lock = acquire_lock()
    if lock is None:
        info = read_info()
        output.say(f"winphoned is already running (pid {info.pid if info else '?'}).")
        return
    try:
        serve(Orchestrator([AndroidAdapter()], ArtifactStore(app_dir() / "artifacts")))
    finally:
        lock.close()


@daemon_app.command("start")
@handled
def daemon_start(ctx: typer.Context) -> None:
    """Start winphoned in the background if it is not running."""
    version = _client(ctx).healthz()
    _show(ctx, {"running": True, "version": version}, lambda: output.say(f"winphoned {version} is running."))


@daemon_app.command("stop")
@handled
def daemon_stop(ctx: typer.Context) -> None:
    """Stop winphoned."""
    try:
        connect_daemon(autostart=False, timeout=2).shutdown()
    except DaemonUnavailable:
        pass
    _show(ctx, {"running": False}, lambda: output.say("winphoned is stopped."))


@daemon_app.command("status")
@handled
def daemon_status(ctx: typer.Context) -> None:
    """Exit 0 if winphoned is running, 7 if not."""
    from winphone_core.runtime import read_info

    version = connect_daemon(autostart=False, timeout=2).healthz()
    info = read_info()
    data = {"running": True, "version": version, "pid": info.pid if info else None}
    _show(ctx, data, lambda: output.say(f"winphoned {version} is running (pid {data['pid']})."))
