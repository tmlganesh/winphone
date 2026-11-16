"""winphone CLI. Device commands go through the winphoned daemon (started on demand);
setup, doctor and the daemon commands work without it."""

from __future__ import annotations

import functools
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar

import typer
from pydantic import BaseModel, ValidationError
from rich.text import Text
from winphone_core.actions import Action, Target, Wait
from winphone_core.client import DaemonClient, connect_daemon
from winphone_core.errors import DaemonUnavailable, InvalidRequest, WinphoneError
from winphone_core.models import DeviceInfo, Key, not_ready_hint
from winphone_core.observation import Selector, find, to_compact
from winphone_core.permissions import ClientKind

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


def _stdin_is_console() -> bool:
    """True only for a real Windows console. `isatty()` is not enough: it is also True when stdin is the
    NUL device, which is how agent shells (Claude Code's tools) run commands."""
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):  # no stdin, not a real file, or not Windows
        return False


def _kind() -> ClientKind:
    """Human only when typed in a real console. A script, CI job or an agent's shell has no console on
    stdin, so it gets agent rules (otherwise an agent could bypass MCP permissions via the CLI)."""
    return ClientKind.HUMAN if _stdin_is_console() else ClientKind.AGENT


def _client(ctx: typer.Context) -> DaemonClient:
    return connect_daemon(call_timeout=_opts(ctx).timeout, client_kind=_kind())


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
    marks = {"ok": Text("ok", style="green"), "warn": Text("warn", style="yellow"), "fail": Text("FAIL", style="red")}
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
            if hint := not_ready_hint(d.platform, d.state):
                output.say(f"hint: {d.id}: {hint}")

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


# --- Phase 2: observe and act ------------------------------------------------------------------

app_app = typer.Typer(no_args_is_help=True, help="Apps on the phone.")
app.add_typer(app_app, name="app")

WaitText = Annotated[str | None, typer.Option("--wait-text", help="Succeed when this text is on screen.")]
WaitGone = Annotated[str | None, typer.Option("--wait-gone", help="Succeed when this text is gone.")]
WaitApp = Annotated[str | None, typer.Option("--wait-app", help="Succeed when this package is in front.")]
WaitTimeout = Annotated[float | None, typer.Option(
    "--wait-timeout", help="Seconds to wait (default: 3 for 'screen changed', 10 for launch and --wait-* conditions).")]
M = TypeVar("M", bound=BaseModel)
_EXIT_BY_OUTCOME = {"verified": 0, "not_verified": 1, "failed": 1, "unknown": 6}


def _xy(value: str) -> tuple[int, int]:
    x, sep, y = value.partition(",")
    if not sep or not x.strip().isdigit() or not y.strip().isdigit():
        raise InvalidRequest(f"expected X,Y, got {value!r}")
    return int(x), int(y)


def _wait(text: str | None, gone: str | None, app_pkg: str | None, timeout: float | None,
          launching: str | None = None) -> Wait | None:
    conditions = {k: v for k, v in (("text_visible", text), ("text_gone", gone), ("app_foreground", app_pkg)) if v}
    if len(conditions) > 1:
        raise InvalidRequest("use one --wait-* condition")
    if conditions:
        return _model(Wait, **conditions, timeout_s=timeout or 10.0)
    if timeout is None:
        return None  # the engine's default: screen changed (3 s), or app in front (10 s) for launch
    if launching:  # a longer timeout for a launch still means "until the app is in front"
        return _model(Wait, app_foreground=launching, timeout_s=timeout)
    return _model(Wait, revision_changed=True, timeout_s=timeout)


def _model(cls: type[M], **kwargs: Any) -> M:
    """Build an Action/Wait/Target, turning a validation failure into a usage error (exit 2)."""
    try:
        return cls(**kwargs)
    except ValidationError as e:
        raise InvalidRequest("; ".join(str(err["msg"]) for err in e.errors())) from None


def _target(ref: str | None, text: str | None = None, desc: str | None = None, rid: str | None = None,
            at: str | None = None) -> Target | None:
    given = [v for v in (ref, text, desc, rid, at) if v]
    if len(given) > 1:
        raise InvalidRequest("give one target: REF, --text, --desc, --id or --at")
    if ref:
        return _model(Target, ref=ref)
    if at:
        return _model(Target, point=_xy(at))
    if text or desc or rid:
        return _model(Target, selector=Selector(text=text, desc=desc, resource_id=rid))
    return None


def _act(ctx: typer.Context, device: str | None, action: Action, wait: Wait | None) -> None:
    budget = (wait.timeout_s if wait else 10.0) + 30  # the daemon call must outlive the wait
    client = connect_daemon(call_timeout=max(_opts(ctx).timeout, budget), client_kind=_kind())
    r = client.act(client.resolve(device).id, action, wait=wait)
    _show(ctx, r, lambda: output.say(f"{r.outcome}: {r.evidence} ({r.target_ref or '-'}, obs {r.observation_id}, "
                                     f"{r.duration_ms} ms)"))
    if _EXIT_BY_OUTCOME[r.outcome]:
        raise typer.Exit(_EXIT_BY_OUTCOME[r.outcome])


@app.command()
@handled
def observe(
    ctx: typer.Context,
    device: DeviceOpt = None,
    find_text: Annotated[str | None, typer.Option("--find", help="Only elements whose text contains this.")] = None,
    shot: Annotated[bool, typer.Option("--screenshot", help="Also save a screenshot.")] = False,
) -> None:
    """Read the screen as a compact list of elements (e1, e2 ...) to act on."""
    client = _client(ctx)
    obs = client.observe(client.resolve(device).id, screenshot=shot)
    if find_text:
        keep = {e.ref for e in find(obs, Selector(text_contains=find_text))}
        obs = obs.model_copy(update={"elements": [e for e in obs.elements if e.ref in keep]})
    _show(ctx, obs, lambda: output.say(to_compact(obs) + (f"\n# screenshot {obs.screenshot_path}"
                                                          if obs.screenshot_path else "")))


@app.command()
@handled
def tap(
    ctx: typer.Context,
    ref: Annotated[str | None, typer.Argument(help="Element ref from the latest observe, e.g. e7.")] = None,
    device: DeviceOpt = None,
    text: Annotated[str | None, typer.Option("--text", help="Element with exactly this text.")] = None,
    desc: Annotated[str | None, typer.Option("--desc", help="Element with this accessibility description.")] = None,
    rid: Annotated[str | None, typer.Option("--id", help="Element with this resource id.")] = None,
    at: Annotated[str | None, typer.Option("--at", help="Screen point X,Y.")] = None,
    wait_text: WaitText = None, wait_gone: WaitGone = None, wait_app: WaitApp = None, wait_timeout: WaitTimeout = None,
) -> None:
    """Tap an element (or a point) and report whether the screen responded."""
    target = _target(ref, text, desc, rid, at)
    _act(ctx, device, _model(Action, kind="tap", target=target),
         _wait(wait_text, wait_gone, wait_app, wait_timeout))


@app.command()
@handled
def swipe(
    ctx: typer.Context,
    direction: Annotated[str | None, typer.Argument(help="up, down, left or right.")] = None,
    device: DeviceOpt = None,
    on: Annotated[str | None, typer.Option("--on", help="Swipe inside this element ref.")] = None,
    start: Annotated[str | None, typer.Option("--from", help="Start point X,Y.")] = None,
    end: Annotated[str | None, typer.Option("--to", help="End point X,Y.")] = None,
    wait_text: WaitText = None, wait_gone: WaitGone = None, wait_app: WaitApp = None, wait_timeout: WaitTimeout = None,
) -> None:
    """Swipe in a direction (content moves the other way), or from one point to another."""
    action = _model(Action, kind="swipe", direction=direction, target=_target(on, at=start),
                    to=_xy(end) if end else None)
    _act(ctx, device, action, _wait(wait_text, wait_gone, wait_app, wait_timeout))


@app.command("type")
@handled
def type_(
    ctx: typer.Context,
    text: Annotated[str | None, typer.Argument(
        help="Text to put in the field. Omit it for passwords: you are prompted (hidden) or it is read from "
             "stdin, so it never lands in your shell history.")] = None,
    device: DeviceOpt = None,
    into: Annotated[str | None, typer.Option("--into", help="Tap this element ref first.")] = None,
    wait_text: WaitText = None, wait_gone: WaitGone = None, wait_app: WaitApp = None, wait_timeout: WaitTimeout = None,
) -> None:
    """Set the text of the focused field (or of --into). The text is never printed or logged."""
    if text is None:
        raw = sys.stdin.read() if not sys.stdin.isatty() else typer.prompt("Text", hide_input=True)
        text = raw.removesuffix("\n").removesuffix("\r")
    _act(ctx, device, _model(Action, kind="type", text=text, target=_target(into)),
         _wait(wait_text, wait_gone, wait_app, wait_timeout))


@app.command()
@handled
def press(
    ctx: typer.Context,
    key: Annotated[Key, typer.Argument(help="back, home, recents or enter.")],
    device: DeviceOpt = None,
    wait_text: WaitText = None, wait_gone: WaitGone = None, wait_app: WaitApp = None, wait_timeout: WaitTimeout = None,
) -> None:
    """Press a system key."""
    _act(ctx, device, _model(Action, kind="press", key=key), _wait(wait_text, wait_gone, wait_app, wait_timeout))


@app.command()
@handled
def apps(ctx: typer.Context, device: DeviceOpt = None,
         include_system: Annotated[bool, typer.Option("--all", help="Include system apps.")] = False) -> None:
    """List installed packages (your own apps unless --all)."""
    client = _client(ctx)
    found = client.apps(client.resolve(device).id, include_system=include_system)
    _show(ctx, found, lambda: output.say("\n".join(found) or "No apps."))


@app_app.command("launch")
@handled
def app_launch(
    ctx: typer.Context,
    package: Annotated[str, typer.Argument(help="Package name, e.g. com.android.settings.")],
    device: DeviceOpt = None,
    wait_text: WaitText = None, wait_gone: WaitGone = None, wait_app: WaitApp = None, wait_timeout: WaitTimeout = None,
) -> None:
    """Launch an app and wait until it is in front."""
    _act(ctx, device, _model(Action, kind="launch", package=package),
         _wait(wait_text, wait_gone, wait_app, wait_timeout, launching=package))


# --- Phase 5: logs and crash reports (Android and iPhone) ----------------------------------------

crashes_app = typer.Typer(help="Crash reports on the phone (iPhone).", invoke_without_command=True)
app.add_typer(crashes_app, name="crashes")


@app.command()
@handled
def logs(
    ctx: typer.Context,
    device: DeviceOpt = None,
    lines: Annotated[int, typer.Option("--lines", help="At most this many lines (1-2000).")] = 200,
    grep: Annotated[str | None, typer.Option("--grep", help="Only lines containing this (any case).")] = None,
) -> None:
    """Device logs: Android's recent logcat; on an iPhone, new lines for up to 5 seconds."""
    client = _client(ctx)
    found = client.logs(client.resolve(device).id, lines=lines, contains=grep)
    _show(ctx, found, lambda: output.say("\n".join(found) if found else "No log lines."))


@crashes_app.callback()
@handled
def crashes(ctx: typer.Context, device: DeviceOpt = None) -> None:
    """List crash reports (use 'crashes pull NAME' to copy one)."""
    if ctx.invoked_subcommand:
        return
    client = _client(ctx)
    found = client.crashes(client.resolve(device).id)
    _show(ctx, found, lambda: output.say("\n".join(found) if found else "No crash reports."))


@crashes_app.command("pull")
@handled
def crashes_pull(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="A name exactly as 'winphone crashes' lists it.")],
    device: DeviceOpt = None,
    out: Annotated[Path | None, typer.Option("--output", "-o", help="Also copy it into this folder.")] = None,
) -> None:
    """Copy one crash report off the phone (kept under the WINPHONE artifacts folder)."""
    client = _client(ctx)
    path = Path(client.pull_crash(client.resolve(device).id, name))
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        path = Path(shutil.copy2(path, out / path.name))
    _show(ctx, {"path": str(path)}, lambda: output.say(str(path)))


# --- Phase 3: Claude Code (MCP), permissions, install, record ----------------------------------

mcp_app = typer.Typer(help="Use WINPHONE from Claude Code. With no subcommand: serve MCP over stdio.",
                      invoke_without_command=True)
app.add_typer(mcp_app, name="mcp")
authorize_app = typer.Typer(no_args_is_help=True, help="Let an AI agent interact with a phone, or take it back.")
app.add_typer(authorize_app, name="authorize")


@mcp_app.callback()
def mcp(ctx: typer.Context) -> None:
    """Serve the MCP tools over stdio (this is what Claude Code starts). Prints nothing else to stdout."""
    if ctx.invoked_subcommand:
        return
    from winphone_mcp.server import main as serve_mcp

    serve_mcp()


def _say_lines(lines: list[str]) -> None:
    for line in lines:
        output.say(line)


@mcp_app.command("install")
@handled
def mcp_install_cmd(ctx: typer.Context) -> None:
    """Register WINPHONE with Claude Code (user scope) and install its agent skill."""
    from winphone_cli import mcp_install

    _say_lines(mcp_install.install())


@mcp_app.command("status")
@handled
def mcp_status_cmd(ctx: typer.Context) -> None:
    """Is WINPHONE registered with Claude Code?"""
    from winphone_cli import mcp_install

    _say_lines(mcp_install.status())


@mcp_app.command("uninstall")
@handled
def mcp_uninstall_cmd(ctx: typer.Context) -> None:
    """Remove WINPHONE and its skill from Claude Code."""
    from winphone_cli import mcp_install

    _say_lines(mcp_install.uninstall())


@authorize_app.command("grant")
@handled
def authorize_grant(ctx: typer.Context, device: DeviceOpt = None,
                    minutes: Annotated[int, typer.Option("--minutes", help="How long (1-240).")] = 30) -> None:
    """Let an AI agent tap, swipe and type on a phone for a while (you confirm in a window)."""
    g = _client(ctx).authorize(device, minutes)
    _show(ctx, g, lambda: output.say(f"granted: agents may interact with {g['device']} for {minutes} minutes"))


@authorize_app.command("list")
@handled
def authorize_list(ctx: typer.Context) -> None:
    """Show which phones agents may interact with, and until when."""
    grants = _client(ctx).grants()
    _show(ctx, grants, lambda: output.table(["PHONE", "UNTIL"], [
        [g["device"], time.strftime("%H:%M", time.localtime(g["expires_at"]))] for g in grants])
          if grants else output.say("No grants: agents may only observe."))


@authorize_app.command("revoke")
@handled
def authorize_revoke(ctx: typer.Context, device: DeviceOpt = None) -> None:
    """Take an agent's permission back (all phones without --device)."""
    n = _client(ctx).revoke(device)
    _show(ctx, {"revoked": n}, lambda: output.say(f"revoked {n} grant(s)"))


@app_app.command("install")
@handled
def app_install(
    ctx: typer.Context,
    apk: Annotated[Path, typer.Argument(help="An .apk file on this PC.")],
    device: DeviceOpt = None,
) -> None:
    """Install (or update) an app. Always asks you to confirm in a window."""
    client = connect_daemon(call_timeout=max(_opts(ctx).timeout, 400), client_kind=_kind())
    name = client.install_app(client.resolve(device).id, str(apk.resolve()))
    _show(ctx, {"installed": name}, lambda: output.say(f"installed {name}"))


@app.command()
@handled
def record(ctx: typer.Context, device: DeviceOpt = None,
           seconds: Annotated[int, typer.Option("--seconds", help="1-60 seconds, video only.")] = 10) -> None:
    """Record the screen into an MP4 file (kept under the WINPHONE artifacts folder)."""
    client = connect_daemon(call_timeout=max(_opts(ctx).timeout, seconds + 60), client_kind=_kind())
    path = client.record(client.resolve(device).id, seconds)
    _show(ctx, {"path": path}, lambda: output.say(path))


@daemon_app.command("run")
@handled
def daemon_run(ctx: typer.Context) -> None:
    """Run winphoned in the foreground (this is what auto-start launches)."""
    from winphone_core.artifacts import ArtifactStore
    from winphone_core.orchestrator import Orchestrator
    from winphone_core.paths import app_dir
    from winphone_core.runtime import acquire_lock, read_info, serve

    from winphone_cli.adapters import load_adapters

    lock = acquire_lock()
    if lock is None:
        info = read_info()
        output.say(f"winphoned is already running (pid {info.pid if info else '?'}).")
        return
    adapters, skipped = load_adapters()
    for reason in skipped:
        output.say(f"skipped {reason} (run winphone doctor)")
    try:
        serve(Orchestrator(adapters, ArtifactStore(app_dir() / "artifacts")))
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
