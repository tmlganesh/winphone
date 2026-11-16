# WINPHONE Phase 3 Implementation Plan — Claude Code (MCP), permissions, npm package

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Milestone 1 of the brief on the owner's Android phone: from Claude Code, through MCP, list the
phone, observe Settings, tap an element by ref and get a `verified` receipt, while the human stays in charge
through permissions. Ship it as a single `winphone.exe` that an npm package installs (`npx -y winphone mcp`),
built and tested locally; **publishing to npm waits for the owner** (end of the project).

**Architecture:** Permissions are enforced in the daemon (the one place every client passes through):
each request says which kind of client it is; agents start with OBSERVE, a human grants INTERACT per phone,
and SENSITIVE actions need a human "Allow" per action. A new `winphone_mcp` package is a thin MCP stdio
server over `DaemonClient`. PyInstaller freezes CLI + daemon + MCP into one folder; a tiny npm package
fetches that folder (checksum-verified) and exposes `winphone`.

**Spec:** `docs/superpowers/specs/2026-11-16-winphone-design.md` §10–§12. Owner, 2026-11-16: Android now,
iPhone later; npm packaging in Phase 3, publish at the end.

## Calls in this plan the owner should check (they refine the approved design)

1. **Approvals use a WINPHONE confirmation window (Allow / Deny) on the laptop's screen**, not Windows Hello.
   Hello is not set up on this laptop, so the design's fallback would have been a typed code in a terminal;
   a native window is clearer and an agent working through Claude Code cannot click it. It times out to
   Deny after 60 s. (If Hello is set up later, it can be added to the same window.)
2. **The CLI counts as a human only when it runs in a real console window.** Run from a script, CI or an
   agent's shell (no console), it gets agent rules. Otherwise Claude could bypass MCP permissions by running
   `winphone tap` in its shell. A deliberate same-user program can still pretend; documented as a limit.
3. **Sensitive = install/uninstall always; any input while a sensitive app is in front (Settings, Play
   Store, messaging, banking/payments by package name, user-editable); taps on targets labelled Send, Pay,
   Buy, Delete, Transfer, Confirm, Uninstall, Reset.** Best effort, stated as not a guarantee.

## Global Constraints

- All earlier constraints hold. Commit only when the owner says. **No `npm publish`, no GitHub release, no
  push** in this phase; npm is tested with a local tarball and a local download folder.
- Agents can never grant themselves anything: `phone_authorize` only *asks*; the answer comes from the
  human's click. `phone_revoke` only removes.
- No arbitrary shell through MCP or HTTP; install takes a local `.apk` path that must exist.
- Audit log `app_dir()/audit.jsonl`: time, client kind, device, action, tier, decision, outcome, approval
  id. Never typed text (length only), never tokens.
- MCP results are compact: observations as `to_compact` text, receipts as one line + JSON; screenshots as a
  file path unless `inline=true`. Screen text is labelled as untrusted device content.
- Claude Code config is changed only by `winphone mcp install` (user scope), reversible by `uninstall`.

## Review Focus

1. **Agent without a grant tries to tap:** refused (`permission_denied`, exit 5) with "ask the human to
   grant"; nothing is sent. → Task 2.
2. **Nobody answers the approval window:** Deny after 60 s, audited, no action. → Task 2.
3. **Agent calls `phone_authorize` repeatedly:** each is one window; refusing or ignoring never grants. → Task 4.
4. **Typed text through MCP:** absent from MCP results, audit log and errors. → Tasks 2, 4.
5. **Frozen exe starts the daemon:** the daemon starts from `winphone.exe` (no `python -m`). → Task 6.

---

### Task 1: Spike — MCP SDK 2.3 and the confirmation window
Add `mcp` to a new `packages/mcp` (`winphone_mcp`); confirm the server API for stdio tools/resources and
text/image results with a 10-line server + the SDK's stdio client. Confirm a topmost Win32 confirmation
window (`MessageBoxW`, MB_YESNO | MB_TOPMOST | MB_SETFOREGROUND, auto-closing at 60 s via
`MessageBoxTimeoutW`) appears from a detached process. Ledger the exact APIs as rulings.

### Task 2: Permissions in core
**Files:** Create `packages/core/src/winphone_core/permissions.py`, `audit.py`; Modify `server.py`,
`client.py`, `models.py`; Test `tests/unit/test_permissions.py`, `tests/security/test_daemon_auth.py`.
**Interfaces:** `Tier(StrEnum)`: OBSERVE, INTERACT, SENSITIVE. `ClientKind`: HUMAN, AGENT (header
`X-Winphone-Client`; missing = AGENT). `SensitivityPolicy(packages: set[str], labels: set[str])` with
`tier_of(action, foreground_app, target_label) -> Tier`. `PermissionManager(confirm: Callable[[str], bool],
audit: AuditLog)`: `check(client, device_id, tier, describe) -> str | None` (approval id or raises
`PermissionDenied` with hint), `grant(device_id, minutes)`, `revoke(device_id | None)`, `grants()`. Human:
OBSERVE+INTERACT allowed, SENSITIVE needs `confirm`. Agent: OBSERVE allowed; INTERACT needs a live grant;
SENSITIVE needs `confirm` each time. Routes check before acting; `/authorize` (asks via the window),
`/revoke`, `/grants`. The confirm function is injected (tests pass a fake; the daemon passes the window).
- [ ] Failing tests for every Review Focus item 1–4, grant expiry, revoke, audit lines (no text). GREEN.

### Task 3: Install and record (Android)
**Files:** Android `adapter.py`/`mapping.py`, core `adapter.py`, `orchestrator.py`, routes, client.
`install_app(serial, apk: Path)` = `adb install -r` (tier SENSITIVE); `record(serial, seconds, dest)` =
scrcpy `--no-window --record=<file> --time-limit=<s>` (1–60 s, tier OBSERVE). iPhone: unsupported, "later".
- [ ] Failing tests (subprocess replaced) incl. missing/non-.apk path refused; GREEN.

### Task 4: MCP server
**Files:** Create `packages/mcp/src/winphone_mcp/server.py`; Test `tests/unit/test_mcp.py` (SDK stdio client
against the server with a daemon over `FakeAdapter`).
Tools (brief §8): `phone_devices, phone_connect, phone_disconnect, phone_capabilities, phone_observe,
phone_screenshot, phone_act, phone_wait, phone_launch_app, phone_install_app, phone_record, phone_logs,
phone_session, phone_authorize, phone_revoke`. Resources: devices, capabilities/{device}, session, receipts.
Every tool: Pydantic-validated args, daemon errors returned as `{error, message, hint}` text (not crashes).
`phone_wait` = new `Orchestrator.wait(device_id, Wait) -> Receipt` (no input; outcome verified/not_verified).
- [ ] Failing tests: tool list exact; observe returns compact text; act without grant → permission_denied;
  after a (fake-confirmed) grant → verified receipt; typed text absent from results. GREEN.

### Task 5: CLI — mcp, authorize, app install, record
`winphone mcp` (serve stdio) · `mcp install|status|uninstall` (`claude mcp add --scope user winphone -- <exe> mcp`,
skill copied to `~/.claude/skills/winphone/`; status reads `claude mcp get winphone`) · `authorize list|grant
[-d] [--minutes N]|revoke [-d]` (human console only) · `app install PATH` · `record [-d] --seconds N`.
- [ ] Failing tests: console detection (human vs agent header), install refuses without console approval path,
  mcp install builds the exact `claude` argv (subprocess replaced). GREEN.

### Task 6: Agent skill, frozen build, npm package
**Files:** Create `skills/claude-code/winphone/SKILL.md`, `packaging/winphone.spec`, `packaging/build.py`,
`npm/winphone/{package.json, bin/winphone.js, install.js, README.md}`; Modify `runtime.default_spawn`
(frozen: `[sys.executable, "daemon", "run"]`).
- PyInstaller one-folder build (fast start; u2.jar bundled) → `dist/winphone-win64.zip` + SHA-256.
- npm: `os: ["win32"]`, `bin: winphone`; `install.js` downloads the zip from the release URL **or** a local
  folder (`WINPHONE_DIST`), verifies SHA-256 from `package.json`, unpacks into the package; `bin/winphone.js`
  spawns the exe with stdio inherited.
- [ ] Tests: `test_frozen_spawn_command`; build the exe; `npm pack`; install the tarball into a scratch prefix
  with `WINPHONE_DIST`; `winphone devices` and an MCP `tools/list` through `npx` work. No publish.

### Task 7: Milestone 1 sign-off on the phone
- [ ] Real-device MCP test (marked `android`): SDK stdio client → `phone_devices` → `phone_launch_app`
  Settings → `phone_observe` → `phone_act` tap "About phone" by ref → `verified`; first without a grant
  (refused), then after the owner clicks Allow on the grant window.
- [ ] Full suite with the phone, 0 skipped (Phase 1, 2, 5-Android tests included). `winphone mcp install`, then
  the owner asks Claude in a **new** Claude Code session to open Settings and tap About phone (the live demo).
- [ ] README (MCP setup, permissions, npm), fresh review, fixes with failing-first tests.
