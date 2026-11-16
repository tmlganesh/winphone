# WINPHONE — Design

Date: 2026-11-16 · Status: draft for owner approval

## 1. Intent

WINPHONE lets a person on Windows, and the AI coding agents they run (Claude Code first), see and drive
**physical** Android phones and iPhones: list them, look at the screen, read the accessibility tree, tap,
type, launch and install apps, record, read logs, and replay reusable flows. It is the Windows-first,
Android-first counterpart to `govijr/terminal-phone`, which is macOS + iOS only (Swift, idb, WebDriverAgent)
and has no Android support at all.

Why it matters: an agent that builds a mobile app should be able to check its work on a real phone from a
Windows laptop, cheaply in tokens, without a human relaying screenshots, and without being able to do
anything on the phone the human has not allowed.

## 2. Goals, non-goals, anti-goals

**Goals**
- One device orchestrator behind three front ends: a `winphone` CLI, an MCP stdio server, a Tauri desktop app.
- Android is first-class over USB and Wi-Fi. iPhone: verified device services from Windows directly; full UI
  automation through a macOS relay.
- Token-cheap agent loop: compact text observations with element references; screenshots kept as local
  files and sent to the agent only when asked.
- Every action ends in a receipt that says whether its effect was verified, not just that a command ran.
- Human stays in charge: three permission tiers, agents cannot grant themselves anything, everything audited.

**Non-goals (for now)**
- Emulators and simulators (physical devices only; an emulator shows up in `adb devices` and works by
  accident, but is not tested or advertised).
- Reimplementing adb, scrcpy, UiAutomator or WebDriverAgent.
- Cloud or multi-machine hosting of the daemon. Everything is local to one Windows user, plus the Mac relay.
- Replacing Xcode/XCTest on Windows.

**Anti-goals (failure even if it "works")**
- A capability shown as supported that the device cannot actually do.
- Any success message, device or screen in the UI that did not come from a real device.
- An action silently re-run after a timeout.
- An agent able to approve its own sensitive action through WINPHONE's own tools.
- Full screenshots or full UI trees pushed into the agent's context on every step.

## 3. Constraints

- **Hardware available:** an Android phone, an iPhone and a Mac (owner, 2026-11-16).
- **This laptop today:** Windows 11, Python 3.12/3.13/3.14, Node 24, WebView2 157, Git, Visual Studio
  installer present. **Missing:** adb (platform-tools), scrcpy, Rust toolchain, uv, Appium, libimobiledevice.
  Each must be downloaded with the owner's OK, from the official release, version pinned, SHA-256 checked.
- **Owner's git rules override the brief:** the repo is created with `git init` but nothing is committed,
  branched, pushed or tagged until the owner says so in chat. GitHub Actions files are written but not run
  until then.
- **Redistribution:** Android SDK Platform-Tools may not be bundled (SDK licence). Apple software is never
  bundled. Both are fetched on the user's machine by `winphone setup` with pinned checksums.
- **Safety on real phones:** automated and integration tests only touch system apps with no account data
  (Settings, Calculator, Clock). Nothing sends messages, pays, or changes accounts or security settings.
- Python 3.12 for the project venv (3.11+ per brief; 3.14 skipped until every dependency ships wheels for it).

## 4. Approaches

How the CLI, MCP server and desktop app share one orchestrator.

| | A. One shared local service (**picked by owner**) | B. Same library inside each tool |
|---|---|---|
| Shape | `winphoned` daemon owns every phone; CLI, MCP and Tauri are thin clients | CLI, MCP and a Tauri sidecar each import the package and drive phones |
| Device ownership | one process per phone, no collisions | two tools can start UiAutomator2 / scrcpy on one phone at once |
| Permissions | a revoke in the app reaches Claude immediately | each process has its own grants; revoke does not propagate |
| Human-cancels-agent | possible (one input queue per phone) | not possible across processes |
| Cost | daemon lifecycle + authenticated local IPC | none |

Dropped: a hosted server (brief requires local-first). B meets "one orchestration layer" in code only, not at
runtime, which defeats the permission model.

## 5. Architecture

```
winphone CLI ──┐
MCP (stdio) ───┼──► winphoned (127.0.0.1, random port, bearer token)        Mac (Phase 6)
Tauri app ─────┘      Orchestrator                                          ┌──────────────────────┐
                        ├ DeviceRegistry   (discovery, hot-plug events)     │ winphone-relay       │
                        ├ SessionManager   (who holds which phone)          │ Appium XCUITest/WDA  │
                        ├ PermissionManager + Approvals                     └──────────▲───────────┘
                        ├ ObservationStore (revisions, element refs)                   │ TLS + token
                        ├ ActionEngine     (lifecycle + receipts)                      │
                        ├ ArtifactStore    (screenshots, recordings)                   │
                        ├ AuditLog         (JSONL, redacted)                           │
                        └ Adapters: AndroidAdapter │ WindowsIOSAdapter │ MacIOSRelayAdapter ─┘
```

### 5.1 Daemon (`winphoned`)
- FastAPI + WebSockets on `127.0.0.1`, OS-assigned port. On start it writes
  `%LOCALAPPDATA%\winphone\daemon.json` = `{port, token, pid, version}`. `%LOCALAPPDATA%` is per-user by
  default ACL; `doctor` verifies no other principal can read the file.
- Every HTTP and WS request needs `Authorization: Bearer <token>`. The `Host` header must be
  `127.0.0.1:<port>`; a request carrying an `Origin` is refused unless it is the Tauri app's origin
  (stops browser pages and DNS rebinding).
- Single instance per user: a lock in the same folder; a second start exits 0 and reports the running one.
- Auto-start: the first CLI or MCP call that needs it spawns it detached (no console window) and waits for
  `/healthz`. `winphone daemon start|stop|status` for explicit control. `winphone doctor` works without it.
- Lifetime (my call): a per-user background process that outlives whoever started it. It is spawned with
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` so closing Claude Code or a
  terminal does not kill it, and it exits on its own after 30 minutes with no connected client, no held
  session and no running recording.
- Events (device added/removed, revision changed, approval requested, action finished) go out on one
  `/events` WebSocket; the desktop app and MCP resources use it. No client polls faster than 10 s.

### 5.2 DeviceAdapter interface
A Python `Protocol`; the orchestrator never touches adb, scrcpy or WDA directly.

```
discover() -> list[DeviceInfo]                  connect(id) / disconnect(id)
capabilities(id) -> set[Capability] + reasons   observe(id, screenshot: bool) -> RawObservation
screenshot(id) -> PNG bytes                     perform(id, Action) -> RawResult
launch_app / install_app / list_apps            logs(id, filter, lines) / record_start / record_stop
live_view(id) -> how to view (Phase 1: spawn scrcpy; Phase 4: stream endpoint)
```

`Capability` is an enum: `OBSERVE_TREE, SCREENSHOT, TAP, SWIPE, TYPE, KEYS, LAUNCH_APP, LIST_APPS,
INSTALL_APP, LOGS, RECORD, LIVE_VIEW, WIRELESS`. Capabilities are computed per device from what was actually
probed (for example: USB debugging authorised, UiAutomator helper running, scrcpy present, Android version),
each missing one with a reason. Calling an unsupported capability returns `capability_unsupported` with that
reason; it is never emulated by a weaker path silently.

### 5.3 Per-phone work queue
One FIFO queue per phone for agent work; human input from the desktop viewer takes priority, cancels queued
agent actions, and bumps the screen revision so the agent's element references go stale.

## 6. Android adapter

| Need | Tool | Notes |
|---|---|---|
| discovery, hot-plug, shell, install, screencap, logcat | `adbutils` (MIT) against the pinned adb server | arg lists, never `shell=True`; every call has a timeout |
| wireless | `adb pair host:port code`, `adb connect host:port` | Android 11+; on Android 10 and older, `adb tcpip 5555` over USB first, then `connect` |
| UI tree, semantic actions, text entry | `uiautomator2` Python lib (openatx, MIT) | pushes its helper to the phone on first connect; `doctor` and the desktop app say so |
| live view, recording | scrcpy (pinned Windows release) | Phase 1: `winphone view` opens scrcpy's own window; recording via `scrcpy --no-window --record` |
| keys | `input keyevent` / uiautomator2 `press` | back, home, recents, enter |
| apps | `pm list packages`, `monkey -p <pkg> -c android.intent.category.LAUNCHER 1`, `adb install -r` | |

Vendor skins (Xiaomi/MIUI, Oppo/ColorOS, some Samsung builds) can block input injection or the helper
install until an extra developer toggle is on (e.g. MIUI "USB debugging (Security settings)"). Capability
probes test injection and the helper for real; when one fails, `doctor` names the vendor toggle to turn on
and the capability stays unsupported. The owner's phone model and Android version are read from the device
by `doctor`; nothing in the design assumes a brand.

Appium's UiAutomator2 driver is **not** used for Android: the `uiautomator2` library talks to the same
on-device UiAutomator framework without a Node/Appium server in the loop, which is faster and one fewer
moving part. (Builder may revisit if a specific gap appears; record why.)

## 7. iPhone

**Mode A, Windows direct (Phase 5).** Detection, metadata, pairing/trust state, syslog, and whatever else is
proven on the owner's iPhone. Requires Apple Mobile Device Service (from Apple's "Apple Devices" app or
iTunes; not bundled). Tooling decided by a Phase 5 spike between libimobiledevice Windows builds (LGPL,
called as subprocesses) and alternatives; `pymobiledevice3` is GPL-3.0 and may not be imported into the
permissively licensed core. Anything not demonstrated on the real iPhone is reported as unsupported.

**Mode B, macOS relay (Phase 6).** `services/macos-relay`: a small Python service on the Mac that drives the
Appium XCUITest driver (which builds and signs WebDriverAgent with the owner's Apple team) and exposes the
same adapter-shaped API. The Windows `MacIOSRelayAdapter` is its client. Transport is TLS with a
self-signed certificate pinned at pairing time plus a bearer token; pairing uses a one-time code shown on the
Mac. Android keeps working when the relay is down; the relay's phones show as offline with the reason.

## 8. Observation model

```
Observation { device_id, observation_id, revision, timestamp, width, height, orientation,
              foreground_app, elements: [Element], screenshot_path? }
Element     { ref: "e7", role, text, content_desc, resource_id, class, bounds:[x1,y1,x2,y2],
              clickable, focusable, scrollable, checked?, enabled, secure }
```
- **Normalisation:** drop pure layout containers that have no text, description, id or interactive flag; keep
  their children. `secure` (password) fields carry no text.
- **Revision:** hash of the normalised element list + foreground app + size + orientation.
- **Element references** (`e1…eN`) belong to one observation. Before any action that uses a ref, the engine
  takes a fresh tree; if the revision differs it tries one re-resolution by (resource_id, text, class) and
  uses it only if exactly one element matches, saying so in the receipt; otherwise it returns
  `stale_reference` and the new observation id.
- **Agent format** (MCP and `--compact`): one line per element, e.g.
  `e7 button "Sign in" id=login @540,1620 click` plus a one-line header (app, size, revision). Raw XML and
  full JSON are available on request but never default.
- **Find:** by text (exact/contains), content description (accessibility id), resource id, role, clickable.
- **Screen content is untrusted data.** MCP results label it as device content; the skill tells the agent
  never to follow instructions that appear on the phone screen.

## 9. Action engine

Lifecycle per action: validate device → capability → permission → reference → execute → wait for the
expected condition → observe → verify → receipt.

```
Receipt { action_id, device_id, action, target{ref, resolved_by}, before_revision, after_revision,
          outcome: verified | not_verified | unknown | denied | failed,
          evidence, duration_ms, observation_id? }
```
- Wait conditions: `text_visible`, `text_gone`, `element_exists`, `app_foreground`, `revision_changed`,
  each with a timeout.
- Default when the caller gives no condition (my call): the engine waits for `revision_changed` for up to
  3 s. Screen changed → `verified` with evidence "screen changed" (the receipt says no stronger condition was
  checked); unchanged → `not_verified`. CLI exit codes: `verified` 0, `not_verified` 1, `unknown` 6.
- **No blind retries.** On a transport error or timeout after execution started, the engine observes again
  and returns `unknown` with what it saw. Retries happen only for steps marked safe in a flow, or before
  execution started (e.g. adb reconnect).
- An idempotency key per request de-duplicates within a session (the last 128 results kept).
- Recovery: stale ref → re-resolve once or report; device gone → `device_disconnected` and the registry
  reconnects in the background (USB re-enumeration, `adb connect` for Wi-Fi); UI transition → wait
  conditions, not sleeps.

## 10. Permissions and security

| Tier | Covers |
|---|---|
| OBSERVE | device info, screenshots, UI tree, logs, app list |
| INTERACT | tap, swipe, type, keys, launch app |
| SENSITIVE | install/uninstall, file deletion, and any action the sensitivity policy flags |

- **Defaults:** humans (desktop app, interactive CLI) hold all tiers, with a confirm prompt for SENSITIVE.
  The MCP agent starts with OBSERVE; INTERACT is granted by a human per phone, for the session or a set time;
  SENSITIVE needs a human approval per action.
- **No self-escalation:** `phone_authorize` only *requests*; it creates a pending approval that a human
  approves in the desktop app or with `winphone authorize approve <id>`. `phone_revoke` lets an agent drop its
  own grants.
- **How an approval proves a human is there (my call):** every approval, from the CLI or the desktop app,
  goes through a **Windows Hello** prompt (`Windows.Security.Credentials.UI.UserConsentVerifier`: PIN,
  fingerprint or face), which an agent cannot answer. If Hello is not set up, the CLI falls back to requiring
  a real console (a console window handle and a TTY on stdin) and typing back a 6-character code that the
  daemon generates for that request. `doctor` warns that this fallback is weaker. The MCP server has no
  approve tool and the HTTP approve route requires a Hello-verified (or console-verified) proof issued by
  the daemon, so a bare token cannot approve.
- **Known limit:** any program running as the same Windows user can read the token, read artifacts, and in
  principle drive the fallback console path. WINPHONE closes the paths through its own tools and documents
  the rest; Windows Hello is the recommended setup.
- **Sensitivity policy (my call; best effort, documented as not a guarantee):** action types that are always
  sensitive (install, uninstall, file delete); a user-editable list of sensitive apps (Settings, Play Store,
  messaging, banking/payment apps by package name) where any INTERACT action is treated as SENSITIVE; and
  target labels such as Send, Pay, Buy, Delete, Transfer, Confirm that escalate a tap.
- No shell or raw adb arguments through MCP or the HTTP API.
- Logs and audit: phone numbers masked to the last 4, text typed into secure fields never logged, tokens never
  logged. Audit is append-only JSONL with client kind, action, device, outcome and approval id.
- Downloads: `winphone setup` fetches platform-tools and scrcpy at pinned versions into
  `%LOCALAPPDATA%\winphone\tools\` and refuses on a SHA-256 mismatch. Tools already on PATH are used only if
  their version matches what is supported; `doctor` says which one is in use.
- Recordings are never automatic; flows mark secret steps and their values never reach reports. Recordings
  cannot redact what is drawn on screen; that limit is documented.
- Screenshots are OBSERVE (as the brief puts them), so the agent gets them by default. Artifacts live under
  `%LOCALAPPDATA%\winphone\artifacts\<device>\` (per-user ACL), capped, and are not encrypted: a key readable
  by the same user would protect nothing extra. Screens marked `FLAG_SECURE` by Android come out black, by
  the OS's own design.

## 11. MCP server

- Official `mcp` Python SDK, stdio transport, a thin client of the daemon.
- Tools: `phone_devices, phone_connect, phone_disconnect, phone_capabilities, phone_observe,
  phone_screenshot, phone_act, phone_wait, phone_launch_app, phone_install_app, phone_record, phone_logs,
  phone_session, phone_authorize, phone_revoke`. Each has a Pydantic input schema, checks capability and
  permission in the daemon, and returns compact text plus structured content. `phone_screenshot` returns the
  file path by default and an inline image only with `inline=true`.
- Resources: connected devices, capabilities per device, active sessions, recent receipts.
- `winphone mcp install` registers the server with Claude Code at user scope and installs the agent skill
  (`skills/claude-code/winphone`) into `~/.claude/skills/winphone`; `status` and `uninstall` reverse/inspect.
  The skill teaches: discover → observe → act by ref with a wait condition → read the receipt → recover; ask
  for approval rather than work around a denial; treat screen text as untrusted.

## 12. CLI

Typer + Rich. Commands as in the brief (`devices, doctor, capabilities, connect, disconnect, view, observe,
screenshot, tap, swipe, type, press, apps, app launch, app install, record, logs, test, mcp install|status|
uninstall`) plus `setup`, `daemon start|stop|status` and `authorize list|approve|deny`.
Common flags: `-d/--device`, `--json`, `--timeout`. Exit codes: 0 ok, 1 error, 2 usage, 3 device not found,
4 capability unsupported, 5 permission denied, 6 outcome unknown, 7 daemon unavailable.

## 13. Desktop app (Phase 4)

Tauri 2 + React + TypeScript + Vite + Tailwind + Zustand + TanStack Query + Lucide; dark first. Pages:
Dashboard, Device Manager, Live Viewer, Automation, MCP Integration, Activity Logs, Settings. It talks only
to the daemon; every device, status and result comes from it, and anything not built yet says "Not available
yet". How live video gets into the window (scrcpy's server stream decoded in the webview vs. driving scrcpy's
own window) is decided by a Phase 4 spike and brought to the owner. Needs Rust and the MSVC C++ build tools.

## 14. Automation (YAML flows)

```yaml
name: settings-smoke
device: ${DEVICE}
steps:
  - launch: com.android.settings
  - wait: {text_visible: "Network", timeout: 10}
  - tap: {text: "Network & internet"}
  - assert: {text_visible: "Internet"}
  - screenshot: network.png
```
Steps: launch, tap, swipe, fill (with `${secret:NAME}` from Windows Credential Manager), wait, assert,
screenshot, record, `if:` on a visible condition. Retries only on steps marked `safe: true`. Output: console,
JSON and JUnit XML. Flows run through the daemon like any other client.

## 15. Repository and packaging

```
apps/desktop               Tauri app
packages/core              orchestrator, models, permissions, audit, daemon (winphone_core)
packages/adapters/android  winphone_android
packages/adapters/ios      winphone_ios (Windows-direct + relay client)
packages/mcp               winphone_mcp
packages/cli               winphone_cli (console script `winphone`)
packages/automation        winphone_automation
services/macos-relay       standalone, its own pyproject, buildable on the Mac alone
skills/claude-code/winphone, examples, tests/{unit,integration,android,ios-relay,security}, scripts, docs
```
A uv workspace (one lockfile, pinned versions), Ruff, Mypy strict, Pytest. Real-device tests are marked
(`android`, `ios_relay`) and run only when `WINPHONE_TEST_ANDROID=<serial>` / relay variables are set; they
are never skipped silently in a phase sign-off.

## 16. Phases and acceptance

| Phase | Scope (from the owner's brief) |
|---|---|
| 1 | repo setup, core architecture, DeviceAdapter, adb discovery, Android connection, screenshot, scrcpy, `doctor` |
| 2 | Android actions, UiAutomator2, accessibility inspection, observation references, action verification, error recovery |
| 3 | MCP server and tools, CLI, Claude Code installer, agent skill, permission system |
| 4 | Tauri desktop app: live viewer, device manager, automation UI, settings, logs |
| 5 | Windows-direct iPhone: libimobiledevice integration, pairing and metadata, capability reporting |
| 6 | macOS relay: WebDriverAgent, XCUITest, secure remote link, real iPhone automation |
| 7 | multi-device improvements, performance, security audit, Windows installer, docs, CI/CD, release packaging |

The CLI command list in §12 is the brief's full list. A phase is done only when the code exists, its tests
pass (including the real-device tests for that phase), the docs and known limitations are updated, and the
owner has seen the result. Each phase gets its own implementation plan when it starts.

**Phase 1 done means, on the owner's Android phone:**
1. `winphone setup` downloads pinned platform-tools and scrcpy, checksum-verified.
2. `winphone doctor` reports Python, adb, scrcpy (version, source, checksum), daemon state, each phone and its
   authorisation state, and what is missing for later phases (Rust, MSVC, Apple Mobile Device Service), with
   a fix line for each.
3. `winphone devices` / `connect` / `disconnect` (USB and Wi-Fi pairing) work on the real phone.
4. `winphone screenshot` saves a PNG whose size matches the phone's display.
5. `winphone view` opens the live scrcpy window for the chosen phone.
6. `winphone capabilities` shows what was actually probed, with reasons for anything missing.
7. The daemon auto-starts, refuses requests without the token or with a foreign `Origin`, and serves the CLI.
8. Unit tests and the Android real-device tests pass.

**Milestone 1 (end of Phase 3):** from Claude Code via MCP: list the phone, observe Settings, tap an element by
ref, and get a `verified` receipt, while the screen is visible live in scrcpy.
**Milestone 2 (end of Phase 6):** iPhone services from Windows directly, then full authorised UI automation
through the Mac relay, demonstrated on the real iPhone.

## 17. Open for the owner (needed before Phase 6, not before)

- How the Mac is reachable from this laptop (same LAN, VPN, or neither) and whether the network isolates
  clients from each other. This decides the relay transport and pairing.
- Apple developer team: paid or free. A free team means WebDriverAgent re-signing every 7 days and tight
  app-ID limits. Also: the iPhone's iOS version, and whether Developer Mode is on.

## 18. Left to the builder

- Exact pinned versions of every dependency and tool (latest stable at build time, recorded with checksums).
- Daemon idle-shutdown policy, log rotation sizes, artifact caps (default: keep the last 50 screenshots per
  phone).
- Internal module boundaries inside each package, HTTP route shapes, and the MCP text format details, within
  the rules above.
- Licence (MIT or Apache-2.0) after the Phase 7 dependency review; MIT is the working assumption.
