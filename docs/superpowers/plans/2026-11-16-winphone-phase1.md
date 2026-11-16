# WINPHONE Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the owner's real Android phone, from Windows: pinned tool setup, `doctor`, device listing, USB/Wi-Fi connect, checksum-verified tools, a screenshot that matches the display, a live scrcpy view, and probed capabilities, all through one authenticated local daemon.

**Architecture:** A per-user `winphoned` daemon (FastAPI on 127.0.0.1, bearer token) owns an `Orchestrator` that talks to phones only through `DeviceAdapter` implementations; Phase 1 ships `AndroidAdapter` (adbutils + pinned adb + pinned scrcpy). The `winphone` CLI is a thin HTTP client that auto-starts the daemon; `doctor` and `setup` work without it.

**Tech Stack:** Python 3.12, uv workspace, Pydantic 2, FastAPI + uvicorn, httpx, Typer + Rich, adbutils, Pytest, Ruff, Mypy (strict).

**Spec:** `docs/superpowers/specs/2026-11-16-winphone-design.md`

## Global Constraints

- Repo root `C:\Users\<you>\Downloads\winphone`. `git init` only. **No commit, branch, tag or push steps** (owner's rule): every task ends with "leave uncommitted".
- Python 3.12 venv via `uv venv --python 3.12`; every dependency pinned in `uv.lock`.
- Only these packages exist after Phase 1: `packages/core` (`winphone_core`), `packages/adapters/android` (`winphone_android`), `packages/cli` (`winphone_cli`). No empty placeholder packages for later phases.
- Nothing outside `tests/` may contain a fake adapter, fake device or canned success.
- Every subprocess: argument list, never `shell=True`, explicit `timeout=` (except the long-running scrcpy viewer).
- All state under `app_dir()` = `%LOCALAPPDATA%\winphone`, overridable by env `WINPHONE_HOME` (tests always set it to a tmp dir).
- The daemon token is never printed, logged or put in an error message.
- Exit codes: 0 ok, 1 error, 2 usage/ambiguous device, 3 device not found/not ready, 4 capability unsupported, 5 permission denied, 6 outcome unknown, 7 daemon unavailable.
- Phase 1 real-device tests only list, probe, screenshot and open the viewer. No taps, no typing, no installs.
- Gates before calling a task done: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy packages` all clean.

## Review Focus

1. **Phone plugged in but USB debugging not yet accepted** (`unauthorized`): `devices` shows it with state `unauthorized` and the hint "Unlock the phone and accept 'Allow USB debugging'"; `screenshot` exits 3 with that hint, no traceback. → Task 4 `test_screenshot_on_unauthorized_device_raises_not_ready`, Task 5 `test_device_info_unauthorized`.
2. **Phone unplugged between listing and acting:** exit 3 `device_not_found`, the daemon keeps running. → Task 4 `test_screenshot_after_device_vanishes`.
3. **Two phones, no `--device`:** refuse with exit 2 listing both ids; never pick one silently. One online phone → auto-selected. → Task 4 `test_resolve_*`.
4. **Stale `daemon.json`** (daemon died, or another process now holds the port): the client starts a fresh daemon instead of failing. → Task 7 `test_stale_daemon_file_respawns`.
5. **Wi-Fi serial with a colon** (`192.168.1.5:5555`) used in a file path: artifact folders are sanitised, Windows accepts the path. → Task 3 `test_wifi_serial_dir_is_windows_safe`.

---

### Task 1: Workspace and core contracts

**Files:**
- Create: `pyproject.toml` (workspace root), `.gitignore`, `.python-version` (`3.12`)
- Create: `packages/core/pyproject.toml`, `packages/core/src/winphone_core/{__init__,models,errors,adapter,paths}.py`
- Test: `tests/unit/test_contracts.py`

**Interfaces:**
- Produces (`winphone_core.models`):
  - `Platform(StrEnum)`: `ANDROID="android"`, `IOS="ios"`
  - `DeviceState(StrEnum)`: `ONLINE="online"`, `UNAUTHORIZED="unauthorized"`, `OFFLINE="offline"`
  - `Transport(StrEnum)`: `USB="usb"`, `WIFI="wifi"`
  - `DeviceInfo(BaseModel)`: `id: str` (`"android:<serial>"`), `platform`, `serial: str`, `state`, `transport`, `model: str | None`, `os_version: str | None`, `sdk: int | None`, `held: bool = False`
  - `Capability(StrEnum)`: `OBSERVE_TREE, SCREENSHOT, TAP, SWIPE, TYPE, KEYS, LAUNCH_APP, LIST_APPS, INSTALL_APP, LOGS, RECORD, LIVE_VIEW, WIRELESS` (lower-case values)
  - `CapabilityStatus(BaseModel)`: `capability`, `supported: bool`, `reason: str | None`
  - `ScreenshotArtifact(BaseModel)`: `device_id: str`, `path: str`, `width: int`, `height: int`, `taken_at: datetime`
- Produces (`winphone_core.errors`): `WinphoneError(Exception)` with class attrs `code: str`, `exit_code: int`, init `(message: str, hint: str | None = None)`, `to_dict() -> {"error", "message", "hint"}`, and `error_from_dict(d) -> WinphoneError` (looks up the subclass by `code`, falls back to base). Subclasses (code, exit): `DeviceNotFound("device_not_found",3)`, `DeviceNotReady("device_not_ready",3)`, `DeviceAmbiguous("device_ambiguous",2)`, `CapabilityUnsupported("capability_unsupported",4)`, `PermissionDenied("permission_denied",5)`, `OutcomeUnknown("outcome_unknown",6)`, `DaemonUnavailable("daemon_unavailable",7)`, `ToolMissing("tool_missing",1)`, `ChecksumMismatch("checksum_mismatch",1)`, `AdapterError("adapter_error",1)`.
- Produces (`winphone_core.adapter`): `DeviceAdapter(Protocol)`:
  `platform: Platform`; `discover() -> list[DeviceInfo]`; `capabilities(serial: str) -> list[CapabilityStatus]`; `screenshot(serial: str) -> bytes` (PNG); `live_view(serial: str) -> int` (viewer pid); `pair(host: str, port: int, code: str) -> None`; `connect_wifi(host: str, port: int) -> str` (serial); `disconnect_wifi(serial: str) -> None`.
- Produces (`winphone_core.paths`): `app_dir() -> Path` (creates it).

- [ ] **Step 1: Owner-approved bootstrap.** `git init`; `python -m pip install --user uv==<latest stable>`; `uv venv --python 3.12`. Workspace root declares members `packages/core`, `packages/adapters/android`, `packages/cli`, dev group `pytest, ruff, mypy`, pytest marker `android: needs WINPHONE_TEST_ANDROID=<serial>`, ruff `line-length=110`, mypy `strict = true`. Packages use hatchling, src layout.
- [ ] **Step 2: Write failing tests**

```python
def test_error_roundtrip_keeps_class_and_exit_code():
    e = error_from_dict(DeviceAmbiguous("2 phones", hint="pass --device").to_dict())
    assert type(e) is DeviceAmbiguous and e.exit_code == 2 and e.hint == "pass --device"

def test_unknown_error_code_falls_back_to_base():
    e = error_from_dict({"error": "nope", "message": "x", "hint": None})
    assert type(e) is WinphoneError and e.exit_code == 1

def test_app_dir_honours_winphone_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WINPHONE_HOME", str(tmp_path / "h"))
    assert app_dir() == tmp_path / "h" and app_dir().is_dir()
```
- [ ] **Step 3:** `uv run pytest tests/unit/test_contracts.py -q` → FAIL (import error).
- [ ] **Step 4:** Implement the four modules above.
- [ ] **Step 5:** Re-run → PASS; ruff + mypy clean. Leave uncommitted.

---

### Task 2: Pinned tool manager

**Files:**
- Create: `packages/core/src/winphone_core/tools.py`
- Test: `tests/unit/test_tools.py`

**Interfaces:**
- Produces: `ToolSpec` (frozen dataclass): `name, version, url, sha256, exe` (`exe` = path inside the archive, e.g. `platform-tools/adb.exe`). `TOOLS: dict[str, ToolSpec]` with keys `"adb"`, `"scrcpy"`.
  `install(spec: ToolSpec, *, fetch: Callable[[str], bytes] = http_get, force: bool = False) -> Path` → extracts to `app_dir()/tools/<name>-<version>/`, returns the exe path.
  `resolve(name: str) -> ResolvedTool` (dataclass: `name, path: Path | None, source: Literal["winphone","PATH","missing"], version: str | None`): winphone-managed first, else `shutil.which`, else missing. `version` read by running `<exe> --version` / `adb version` (timeout 10 s).

- [ ] **Step 1: Write failing tests** (build a zip in memory; `fetch` is a lambda returning it)

```python
def test_install_refuses_checksum_mismatch(tmp_home):
    with pytest.raises(ChecksumMismatch): install(spec(sha256="0"*64), fetch=lambda u: ZIP)
    assert not (tmp_home / "tools").exists() or not any((tmp_home / "tools").iterdir())

def test_install_extracts_and_returns_exe(tmp_home):
    assert install(spec(sha256=sha(ZIP)), fetch=lambda u: ZIP).read_bytes() == b"exe"

def test_install_rejects_zip_slip(tmp_home):
    with pytest.raises(ValueError): install(spec(sha256=sha(EVIL)), fetch=lambda u: EVIL)  # member "../x"
    assert not (tmp_home.parent / "x").exists()

def test_resolve_reports_missing(tmp_home, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert resolve("scrcpy").source == "missing"
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Verify SHA-256 of the archive bytes **before** writing anything; extract into a temp dir beside the target and `os.replace` it in; reject any member whose resolved path leaves the target.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Record real pins (owner-approved downloads).** Look up the latest stable Windows platform-tools (versioned URL on `dl.google.com/android/repository/`, checked against the SHA-1 in Google's `repository2-*.xml`) and the latest scrcpy `scrcpy-win64-vX.Y.Z.zip` (checked against the SHA-256 in the GitHub release notes). Put version, URL and the computed SHA-256 in `TOOLS`. Leave uncommitted.

---

### Task 3: Artifact store and PNG size

**Files:**
- Create: `packages/core/src/winphone_core/artifacts.py`
- Test: `tests/unit/test_artifacts.py`

**Interfaces:**
- Produces: `png_size(data: bytes) -> tuple[int, int]` (raises `AdapterError` if not a PNG; reads IHDR with `struct`, no Pillow).
  `ArtifactStore(root: Path, keep: int = 50)`, `.save_screenshot(device_id: str, png: bytes) -> Path` → `root/<safe(device_id)>/<UTC yyyymmddTHHMMSSffffff>.png`, prunes oldest beyond `keep`.

- [ ] **Step 1: Write failing tests**

```python
def test_wifi_serial_dir_is_windows_safe(tmp_path):
    p = ArtifactStore(tmp_path).save_screenshot("android:192.168.1.5:5555", PNG_1x2)
    assert ":" not in str(p.relative_to(tmp_path)) and p.exists()

def test_prunes_beyond_keep(tmp_path):
    s = ArtifactStore(tmp_path, keep=2)
    for _ in range(3): s.save_screenshot("android:x", PNG_1x2)
    assert len(list((tmp_path / "android_x").iterdir())) == 2

def test_png_size_reads_ihdr():
    assert png_size(PNG_1x2) == (1, 2)

def test_png_size_rejects_non_png():
    with pytest.raises(AdapterError): png_size(b"error: device offline")
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (`safe` = replace anything outside `[A-Za-z0-9._-]` with `_`). **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 4: Orchestrator

**Files:**
- Create: `packages/core/src/winphone_core/orchestrator.py`
- Test: `tests/unit/test_orchestrator.py` (with `tests/unit/fakes.py`: `FakeAdapter`, test-only)

**Interfaces:**
- Consumes: Task 1 models/errors/adapter, Task 3 `ArtifactStore`, `png_size`.
- Produces: `Orchestrator(adapters: Sequence[DeviceAdapter], artifacts: ArtifactStore)` with
  `devices() -> list[DeviceInfo]` (sets `held`), `resolve(selector: str | None) -> DeviceInfo`, `connect(device_id) -> DeviceInfo`, `disconnect(device_id) -> None`, `capabilities(device_id) -> list[CapabilityStatus]`, `screenshot(device_id) -> ScreenshotArtifact`, `live_view(device_id) -> int`, `pair_wifi(host, port, code) -> None`, `connect_wifi(host, port) -> DeviceInfo`, `held_count() -> int`.
  Rules: `resolve(None)` → the single ONLINE device, else `DeviceAmbiguous` (message lists ids) or `DeviceNotFound`; a selector matches a full id or a bare serial. Every device operation re-discovers first (no cached device list), raises `DeviceNotFound` if gone, `DeviceNotReady` if not ONLINE (hint for `unauthorized`: "Unlock the phone and accept 'Allow USB debugging'"), and checks the capability (`CapabilityUnsupported` with the adapter's reason) before calling the adapter. `disconnect` on a Wi-Fi device also calls `disconnect_wifi`.

- [ ] **Step 1: Write failing tests**

```python
def test_resolve_single_online_device_auto_selects(): ...   # one online + one unauthorized -> the online one
def test_resolve_two_online_devices_is_ambiguous():
    with pytest.raises(DeviceAmbiguous) as e: orch(two_online).resolve(None)
    assert "android:A" in str(e.value) and "android:B" in str(e.value)
def test_resolve_accepts_bare_serial(): assert orch(one).resolve("A").id == "android:A"
def test_screenshot_on_unauthorized_device_raises_not_ready():
    with pytest.raises(DeviceNotReady) as e: orch(unauth).screenshot("android:A")
    assert "Allow USB debugging" in (e.value.hint or "")
def test_screenshot_after_device_vanishes():
    o = orch(one); fake.devices.clear()
    with pytest.raises(DeviceNotFound): o.screenshot("android:A")
def test_unsupported_capability_is_refused_with_reason(): ...  # fake marks SCREENSHOT unsupported "x" -> CapabilityUnsupported, adapter.screenshot never called
def test_screenshot_saves_artifact_with_png_size(): ...        # width/height == fake PNG size, file exists
def test_connect_holds_and_disconnect_releases(): ...           # held_count 1 -> 0; devices()[0].held flips
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 5: Android adapter

**Files:**
- Create: `packages/adapters/android/pyproject.toml` (deps: `winphone-core`, `adbutils`), `packages/adapters/android/src/winphone_android/{__init__,adapter,mapping}.py`
- Test: `tests/unit/test_android_mapping.py`, `tests/android/test_phase1_device.py`

**Interfaces:**
- Consumes: Task 1 contracts, Task 2 `resolve("adb")`, `resolve("scrcpy")`.
- Produces (`mapping.py`, pure): `device_info_from(serial: str, adb_state: str, props: Mapping[str, str]) -> DeviceInfo` (`device`→ONLINE, `unauthorized`→UNAUTHORIZED, else OFFLINE; transport WIFI if serial has `:` or starts with `adb-`, else USB; model `ro.product.model`, os `ro.build.version.release`, sdk `ro.build.version.sdk`).
  `android_capabilities(state: DeviceState, sdk: int | None, scrcpy_available: bool) -> list[CapabilityStatus]`: one entry per `Capability`. SCREENSHOT supported iff ONLINE. LIVE_VIEW iff ONLINE and scrcpy available (reason "scrcpy not installed: run winphone setup"). WIRELESS supported iff ONLINE (reason text says `adb pair` on SDK ≥ 30, `adb tcpip` below). Everything else unsupported, reason `"Not built yet (Phase 2)"` (RECORD/LOGS/INSTALL_APP/LIST_APPS/LAUNCH_APP too).
- Produces (`adapter.py`): `AndroidAdapter()` implementing `DeviceAdapter`. On init: resolve adb (raise `ToolMissing` with "run winphone setup" if missing), set `ADBUTILS_ADB_PATH` (verify the env var name in the installed adbutils source) and run `adb start-server` (timeout 20 s). `screenshot` = `exec-out screencap -p` bytes; `live_view` = `Popen([scrcpy, "--serial", s, "--window-title", f"WINPHONE - {model or s}"], env={..., "ADB": adb_path})`, and if it exits within 2 s raise `AdapterError` with its stderr; `pair` = `[adb, "pair", f"{host}:{port}", code]` timeout 30 s, success iff output contains "Successfully paired", error text never contains the code; `connect_wifi` via adbutils `connect(..., timeout=10)`.

- [ ] **Step 1: Write failing unit tests**

```python
def test_device_info_unauthorized():
    d = device_info_from("R58M", "unauthorized", {})
    assert d.state is DeviceState.UNAUTHORIZED and d.transport is Transport.USB and d.id == "android:R58M"
def test_device_info_wifi_and_props():
    d = device_info_from("192.168.1.5:5555", "device", {"ro.product.model": "Pixel 7", "ro.build.version.sdk": "34"})
    assert d.transport is Transport.WIFI and d.sdk == 34 and d.model == "Pixel 7"
def test_capabilities_offline_device_supports_nothing():
    assert not any(c.supported for c in android_capabilities(DeviceState.OFFLINE, None, True))
def test_capabilities_phase2_items_say_so():
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, True)}
    assert c[Capability.SCREENSHOT].supported and not c[Capability.TAP].supported
    assert c[Capability.TAP].reason == "Not built yet (Phase 2)"
def test_live_view_unsupported_without_scrcpy(): ...
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement `mapping.py` then `adapter.py`. **Step 4:** Run → PASS.
- [ ] **Step 5: Write the real-device tests** (`@pytest.mark.android`, skip with reason unless `WINPHONE_TEST_ANDROID` is set): `test_device_listed_online`; `test_screenshot_matches_display` (`(w, h)` in `{(W, H), (H, W)}` from `adb shell wm size`, using the "Override size" line if present); `test_capabilities_probed` (SCREENSHOT supported, OBSERVE_TREE reason mentions Phase 2); `test_live_view_starts_scrcpy` (pid alive after 2 s, then terminated). Leave uncommitted.

---

### Task 6: Daemon server and runtime

**Files:**
- Create: `packages/core/src/winphone_core/{server,runtime}.py` (core deps add `fastapi`, `uvicorn`)
- Test: `tests/security/test_daemon_auth.py`, `tests/unit/test_runtime.py`

**Interfaces:**
- Consumes: Task 4 `Orchestrator`, Task 1 errors.
- Produces (`server.py`): `create_app(orch: Orchestrator, token: str, port: int, on_shutdown: Callable[[], None]) -> FastAPI`. `TAURI_ORIGINS = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}`. Middleware order: Host must equal `127.0.0.1:{port}` → 403 `bad_host`; an `Origin` header not in `TAURI_ORIGINS` → 403 `bad_origin`; `Authorization: Bearer` compared with `secrets.compare_digest` → 401 `unauthorized`. `WinphoneError` → JSON `to_dict()` with status 409 (404 for `device_not_found`). Routes: `GET /healthz {version}`, `GET /devices`, `POST /devices/resolve {device: str|null}`, `POST /devices/{id}/connect`, `POST /devices/{id}/disconnect`, `GET /devices/{id}/capabilities`, `POST /devices/{id}/screenshot`, `POST /devices/{id}/view {pid}`, `POST /wifi/pair {host, port, code}`, `POST /wifi/connect {host, port}`, `POST /shutdown`. Route handlers are plain `def` (FastAPI runs them in its thread pool; adapters stay synchronous).
- Produces (`runtime.py`): `DaemonInfo` (pydantic: `port, token, pid, version`); `info_path() -> Path` (`app_dir()/daemon.json`); `write_info(info)` (tmp file + `os.replace`); `read_info() -> DaemonInfo | None`; `acquire_lock() -> IO | None` (`msvcrt.locking` on `app_dir()/daemon.lock`, `None` if held); `should_exit(now: float, last_request: float, held: int, idle_limit: float = 1800) -> bool`; `serve(orch) -> None` (bind a socket on `127.0.0.1:0`, token `secrets.token_urlsafe(32)`, write info, run uvicorn on that socket, idle watcher every 60 s, delete `daemon.json` on exit); `spawn_detached(cmd: list[str]) -> None` (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, retry without breakaway on `PermissionError`/`OSError`, std streams to DEVNULL).

- [ ] **Step 1: Write failing tests** (`TestClient(app, base_url="http://127.0.0.1:4555")`, orchestrator over `FakeAdapter`)

```python
def test_no_token_is_401(c): assert c.get("/devices").status_code == 401
def test_wrong_token_is_401(c): assert c.get("/devices", headers=auth("nope")).status_code == 401
def test_foreign_origin_is_403(c):
    assert c.get("/devices", headers={**auth(T), "Origin": "https://evil.example"}).status_code == 403
def test_tauri_origin_ok(c):
    assert c.get("/devices", headers={**auth(T), "Origin": "tauri://localhost"}).status_code == 200
def test_bad_host_is_403(app):
    assert TestClient(app, base_url="http://evil.example").get("/devices", headers=auth(T)).status_code == 403
def test_error_body_carries_code_and_no_token(c):
    r = c.post("/devices/android:nope/screenshot", headers=auth(T))
    assert r.status_code == 404 and r.json()["error"] == "device_not_found" and T not in r.text
def test_should_exit(): assert should_exit(2000, 100, 0) and not should_exit(2000, 100, 1) and not should_exit(500, 100, 0)
def test_write_read_info_roundtrip(tmp_home): ...
def test_second_lock_is_refused(tmp_home): l1 = acquire_lock(); assert l1 and acquire_lock() is None
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 7: Daemon client with auto-start

**Files:**
- Create: `packages/core/src/winphone_core/client.py` (core deps add `httpx`)
- Test: `tests/unit/test_client.py`

**Interfaces:**
- Consumes: Task 6 routes, `read_info`, `spawn_detached`.
- Produces: `DaemonClient(http: httpx.Client)` with methods mirroring the routes (`devices() -> list[DeviceInfo]`, `resolve(device: str | None) -> DeviceInfo`, `connect`, `disconnect`, `capabilities`, `screenshot -> ScreenshotArtifact`, `view -> int`, `pair_wifi`, `connect_wifi`, `shutdown`), raising `error_from_dict(body)` on non-2xx.
  `connect_daemon(*, autostart: bool = True, spawn: Callable[[], None] = default_spawn, timeout: float = 15) -> DaemonClient`: read info → `GET /healthz` with the token; on missing file, connection error, or 401 → if `autostart`, call `spawn()` and poll until healthy or raise `DaemonUnavailable` (hint: "run winphone daemon run to see why"). `default_spawn` runs `[sys.executable, "-m", "winphone_cli", "daemon", "run"]` via `spawn_detached`.

- [ ] **Step 1: Write failing tests** (`spawn` injected; a `TestClient` stands in for the network)

```python
def test_stale_daemon_file_respawns(tmp_home):
    write_info(DaemonInfo(port=1, token="dead", pid=999999, version="0"))  # nothing listens on :1
    calls = []
    with pytest.raises(DaemonUnavailable): connect_daemon(spawn=lambda: calls.append(1), timeout=0.5)
    assert calls == [1]
def test_no_autostart_raises_without_spawning(tmp_home): ...
def test_error_body_becomes_typed_exception(): ...  # 409 {"error":"capability_unsupported"} -> CapabilityUnsupported
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 8: CLI

**Files:**
- Create: `packages/cli/pyproject.toml` (deps `winphone-core`, `winphone-android`, `typer`, `rich`; script `winphone = "winphone_cli.main:app"`), `packages/cli/src/winphone_cli/{__init__,__main__,main,output}.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: Task 7 `connect_daemon`, Task 6 `serve`/`acquire_lock`, Task 5 `AndroidAdapter`, Task 2 `install`/`TOOLS`.
- Produces commands (global options `--json`, `--timeout FLOAT`; device commands take `-d/--device`):
  `setup [--force]` · `devices` · `connect [-d] [--wifi HOST:PORT] [--pair HOST:PORT --code CODE]` · `disconnect [-d]` · `capabilities [-d]` · `screenshot [-d] [-o PATH]` · `view [-d]` · `daemon run|start|stop|status`.
  `daemon run` is the daemon entry point: `acquire_lock()` (if `None`: print the running daemon's port and pid, exit 0), then `serve(Orchestrator([AndroidAdapter()], ArtifactStore(app_dir() / "artifacts")))`. No later-phase commands are registered yet.
  One error handler: `WinphoneError` → stderr `error: <message>` + `hint: <hint>` (or the `to_dict()` JSON with `--json`), exit `e.exit_code`.

- [ ] **Step 1: Write failing tests** (`typer.testing.CliRunner`; `connect_daemon` monkeypatched to a `DaemonClient` over `TestClient(create_app(orch(FakeAdapter)...))`)

```python
def test_devices_json_lists_ids(): r = run("--json", "devices"); assert r.exit_code == 0 and json.loads(r.stdout)[0]["id"] == "android:A"
def test_ambiguous_device_exit_2(): assert run("screenshot").exit_code == 2           # two online fakes
def test_unsupported_capability_exit_4(): assert run("view", "-d", "A").exit_code == 4  # fake without LIVE_VIEW
def test_screenshot_copies_to_output(tmp_path): ...                                     # -o file exists, same bytes
def test_error_json_shape(): r = run("--json", "screenshot", "-d", "Z"); assert json.loads(r.stdout)["error"] == "device_not_found" and r.exit_code == 3
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 9: Doctor

**Files:**
- Create: `packages/core/src/winphone_core/doctor.py`
- Modify: `packages/cli/src/winphone_cli/main.py` (add the `doctor` command: render checks, `--json` supported, exit 1 if any `fail`; it never starts the daemon)
- Test: `tests/unit/test_doctor.py`

**Interfaces:**
- Produces: `Check` (pydantic: `name, status: Literal["ok","warn","fail"], detail: str, fix: str | None`); `run_checks() -> list[Check]`; pure helpers `parse_adb_devices(out: str) -> list[tuple[str, str]]` and `acl_principals(icacls_out: str) -> set[str]`.
  Checks, in order: Python ≥ 3.11 · adb (path, source, version; `fail` + "run winphone setup" if missing) · scrcpy (same) · daemon (`read_info` + healthz; `warn` if not running) · `daemon.json` ACL (`warn` if a principal other than the current user, `NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators` appears) · one check per phone from `adb devices -l` (`unauthorized` → `fail` with the USB-debugging hint; `offline` → `fail`, "replug the cable"; none at all → `warn`, "Enable Developer options → USB debugging, use a data cable") · later phases as `warn`: Rust (`rustc --version`), MSVC C++ tools (`vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64`), Apple Mobile Device Service (`sc query "Apple Mobile Device Service"`).

- [ ] **Step 1: Write failing tests**

```python
def test_parse_adb_devices():
    out = "List of devices attached\nR58M\tunauthorized usb:1-1 transport_id:2\n10.0.0.5:5555\tdevice product:x\n\n"
    assert parse_adb_devices(out) == [("R58M", "unauthorized"), ("10.0.0.5:5555", "device")]
def test_acl_principals():
    out = r"C:\x\daemon.json NT AUTHORITY\SYSTEM:(F)" "\n" r"                  BUILTIN\Administrators:(F)" "\n" r"                  PC\user:(F)" "\n\nSuccessfully processed 1 files"
    assert acl_principals(out) == {r"NT AUTHORITY\SYSTEM", r"BUILTIN\Administrators", r"PC\user"}
def test_doctor_without_adb_fails_with_fix(monkeypatch): ...   # resolve -> missing => adb check fail, fix mentions "winphone setup"
```
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 10: Real-device sign-off and docs

**Files:**
- Create: `README.md` (what works in Phase 1, setup, phone preparation, troubleshooting, known limitations, link to the design)
- Test: `tests/android/test_phase1_device.py` (from Task 5) plus `test_cli_end_to_end` in the same file

- [ ] **Step 1:** Add `test_cli_end_to_end` (marked `android`): with `WINPHONE_HOME` set to a tmp dir, `winphone --json devices` exits 0 and lists the serial (auto-starts the daemon); `winphone --json screenshot -d <serial>` exits 0 and the path exists; finally `winphone daemon stop` exits 0.
- [ ] **Step 2:** On the owner's phone (USB debugging on, plugged in): `uv run winphone setup` → both tools installed with checksums; `uv run winphone doctor` → adb, scrcpy, phone `ok`.
- [ ] **Step 3:** `WINPHONE_TEST_ANDROID=<serial> uv run pytest -q` → all pass, **0 skipped** (the run that signs off the phase must include the android tests).
- [ ] **Step 4:** Manual checks the owner sees: `winphone view` opens the live scrcpy window; `winphone screenshot -o shot.png` matches what is on the phone; `connect --pair/--wifi` pairs over Wi-Fi and `devices` shows transport `wifi`; unplugging the cable and running `screenshot` exits 3 while the daemon stays up.
- [ ] **Step 5:** Measure: `winphone screenshot` wall time (warm daemon) and `devices` latency, recorded in the README.
- [ ] **Step 6:** Write `README.md` with the results, then the review pass (`/code-review`). Leave uncommitted.
