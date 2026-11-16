# WINPHONE Phase 5 Implementation Plan — iPhone from Windows (no Mac)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** WINPHONE lists the owner's iPhone next to Android phones and offers, from Windows alone: device
details and trust state, installed apps, live logs and crash reports. Everything Apple reserves for its
developer tools (screenshot, launch, tap, type, UI tree) is reported as unsupported with the exact reason.

**Architecture:** A new `winphone_ios` package with `WindowsIOSAdapter`. Discovery and trust use a small
standard-library client for Apple's usbmuxd (127.0.0.1:27015; milliseconds, so Android actions are not
slowed); apps, logs and crash reports call **go-ios** v1.3.2 (MIT, pinned, SHA-256 checked). The daemon
loads every adapter whose tool is present and skips the others with a reason.

**Spec:** `docs/superpowers/specs/2026-11-16-winphone-design.md` §7 Mode A. Owner's choices 2026-11-16: no
Mac for now; build "iPhone basics"; leave app installing out.

## Facts measured on the owner's iPhone (iPhone10,6 = iPhone X, iOS 16.7.16), 2026-11-16

- usbmuxd ListDevices + unpaired lockdown GetValue (ProductType, ProductVersion) work with the standard
  library. The pair record for the iPhone exists (Apple Devices app).
- go-ios v1.3.2 `go-ios-win.zip` (single `ios.exe`), SHA-256
  `939c6bcaafed183a92afb9f79cc11b1f935fa6389bfc94d3902e3f52c4dff3fe` (GitHub digest, verified). Prints JSON
  on stdout and its own log lines on stderr (a harmless "agent is not running" warning on iOS 16). Output
  is UTF-8: decode explicitly, never with the Windows code page.
- Works: `list --details` (JSON), `info` (67 keys, 0.9 s), `apps` / `apps --system` (JSON list of dicts
  with `CFBundleIdentifier`), `syslog` (JSON lines `{"msg": ...}`), `crash ls` (`{"files": [...]}`),
  `devmode get` (`{"DeveloperModeEnabled": false}`), `diagnostics list`.
- Fails without the developer disk image: `screenshot`, `launch` ("instruments service … unavailable").

## Global Constraints

- All Phase 1–2 constraints hold. Commit only when the owner says.
- go-ios is downloaded by `winphone setup` (pinned version + SHA-256), never bundled. No Apple software is
  downloaded: no developer disk image (`ios image auto` is never used; it fetches Apple files from an
  unofficial mirror).
- Every go-ios call: argument list, `--udid=<udid>`, `encoding="utf-8", errors="replace"`, a timeout; a
  non-zero exit becomes `AdapterError` with go-ios's last ERROR line (no secrets pass through it).
- iPhone capabilities never claim more than was verified: LIST_APPS, LOGS, CRASH_REPORTS supported when
  trusted; SCREENSHOT and LAUNCH_APP unsupported ("needs Apple's developer disk image from Xcode on a Mac
  and Developer Mode on"); OBSERVE_TREE/TAP/SWIPE/TYPE/KEYS unsupported ("needs WebDriverAgent signed on a
  Mac, Phase 6"); LIVE_VIEW, RECORD, WIRELESS, INSTALL_APP unsupported with reasons.
- Device logs and crash reports can hold personal data: they are returned to the owner's own client, kept
  in WINPHONE's artifacts folder only when explicitly pulled, never written to the daemon's logs.
- Real-device tests are read-only on the iPhone.

## Review Focus

1. **iPhone not trusted / locked:** listed as `unauthorized` with "Unlock the iPhone and tap Trust", never a
   traceback. → Task 2 `test_untrusted_iphone_is_unauthorized`.
2. **No Apple service (Apple Devices app not installed):** discovery returns no iPhones in milliseconds;
   Android keeps working; doctor says what to install. → Task 2 `test_no_usbmuxd_is_empty_fast`.
3. **go-ios missing:** iPhone still listed; its go-ios capabilities say "run winphone setup". → Task 3.
4. **Android and iPhone both online, no `--device`:** exit 2 listing both. → covered by Phase 1 resolve rules;
   real-device check in Task 6.
5. **go-ios output with non-ASCII bytes:** decoded as UTF-8, never crashes. → Task 3 `test_go_ios_output_is_utf8`.

---

### Task 1: Pin go-ios, tolerate missing tools in the daemon

**Files:** Modify `packages/core/src/winphone_core/tools.py`, `doctor.py`, `packages/cli/src/winphone_cli/main.py`
(`daemon run`); Test `tests/unit/test_tools.py`, `tests/unit/test_doctor.py`, `tests/unit/test_cli.py`.

**Interfaces:** `TOOLS["go-ios"] = ToolSpec(name="go-ios", version="1.3.2", url=".../v1.3.2/go-ios-win.zip",
sha256="939c6bca…3fe", exe="ios.exe")`; `MIN_VERSIONS["go-ios"] = (1, 3)`; `_probe_version` uses `version`
for go-ios. `daemon run` builds adapters with `load_adapters() -> tuple[list[DeviceAdapter], list[str]]`
(skips an adapter whose tool is missing and records why). Doctor: `go-ios` tool check; the Phase 5
Apple-service check becomes "iPhone support" (`ok` when usbmuxd answers).

- [ ] Failing tests: `test_go_ios_pin_present`, `test_load_adapters_skips_missing_tool` (adb missing → only
  the iOS adapter, reason recorded), doctor shows go-ios. Then implement; GREEN.

### Task 2: usbmuxd client and iPhone mapping

**Files:** Create `packages/adapters/ios/{pyproject.toml, src/winphone_ios/{__init__,usbmux,mapping}.py}`;
Test `tests/unit/test_ios_usbmux.py` (a real local TCP server speaking the usbmux plist protocol),
`tests/unit/test_ios_mapping.py`.

**Interfaces:** `usbmux.MuxDevice(device_id: int, udid: str, connection: str)`;
`list_devices(timeout=2.0) -> list[MuxDevice]` (connection refused → `[]`); `has_pair_record(udid) -> bool`;
`lockdown_values(device_id, keys) -> dict[str, str]` (unpaired GetValue). `mapping.device_info_from_ios(udid,
connection, values, trusted) -> DeviceInfo` (id `ios:<udid>`, platform IOS, transport USB/WIFI, model from
ProductType via a small table, else the raw type; os_version; state ONLINE if trusted else UNAUTHORIZED);
`ios_capabilities(state, go_ios_available: bool) -> list[CapabilityStatus]`; `models.NOT_READY_HINTS` gains the
iPhone wording via `IOS_NOT_TRUSTED_HINT`.

- [ ] Failing tests: `test_list_devices_parses_mux_reply`, `test_no_usbmuxd_is_empty_fast`,
  `test_has_pair_record_true_false`, `test_lockdown_values`, `test_untrusted_iphone_is_unauthorized`,
  `test_iphone_x_model_name`, `test_ios_capabilities_honest` (exact supported set and reasons). Implement; GREEN.

### Task 3: WindowsIOSAdapter

**Files:** Create `packages/adapters/ios/src/winphone_ios/adapter.py`; Test `tests/unit/test_ios_adapter.py`.

**Interfaces:** `WindowsIOSAdapter()` implements `DeviceAdapter`: `discover`, `capabilities`, `list_apps`
(bundle ids, sorted; `--system` when include_system), `logs`, `crash_reports`, `pull_crash_report`; every other
method raises `CapabilityUnsupported` with the capability's reason. `_go_ios(args, timeout) -> str` (stdout).

- [ ] Failing tests (subprocess replaced): `test_list_apps_parses_bundle_ids`, `test_go_ios_error_line_surfaces`,
  `test_go_ios_output_is_utf8`, `test_unsupported_methods_say_why`, `test_missing_go_ios_capabilities`.
  Implement; GREEN.

### Task 4: Logs and crash reports in core (both platforms)

**Files:** Modify `models.py` (`Capability.CRASH_REPORTS`), `adapter.py`, `orchestrator.py`, `server.py`,
`client.py`, Android `adapter.py`/`mapping.py`, `tests/fakes.py`; Test `tests/unit/test_logs.py`,
`tests/security/test_daemon_auth.py`.

**Interfaces:** `DeviceAdapter.logs(serial, lines: int, contains: str | None) -> list[str]`;
`crash_reports(serial) -> list[str]`; `pull_crash_report(serial, name, dest: Path) -> Path`. Android: logs =
`adb logcat -d -t <lines>` filtered; crash reports unsupported ("not available without root"). iPhone logs =
read `syslog` for up to 5 s or `lines` lines, whichever first. Orchestrator `logs(...)`, `crash_reports(...)`,
`pull_crash_report(device_id, name) -> str` (into `artifacts/<device>/crashes/`; `name` must be one of the
listed files — never a path). Routes: `GET /devices/{id}/logs?lines=&contains=`, `GET /devices/{id}/crashes`,
`POST /devices/{id}/crashes/pull {name}`.

- [ ] Failing tests: `test_logs_capability_checked`, `test_logs_lines_bounded` (1..2000), `test_pull_rejects_unlisted_name`
  (`../x`, a name not in the list), route + client round-trips. Implement; GREEN.

### Task 5: CLI

**Files:** Modify `packages/cli/src/winphone_cli/main.py`; Test `tests/unit/test_cli.py`.

**Commands:** `logs [-d] [--lines N] [--grep TEXT]`, `crashes [-d]`, `crashes pull NAME [-d] [-o DIR]`.
`devices` and `capabilities` show iPhones with no changes beyond the adapter.

- [ ] Failing tests: `test_logs_prints_lines`, `test_crashes_lists`, `test_logs_on_unsupported_phone_exit_4`. GREEN.

### Task 6: Real-device sign-off, docs, review

**Files:** Create `tests/ios/test_phase5_iphone.py` (marked `ios`, runs when `WINPHONE_TEST_IOS=<udid>`); Modify
`README.md`.

- [ ] Tests on the iPhone: listed `online` with model "iPhone X" and iOS 16.7.16; capabilities exactly as in
  Global Constraints; `apps --all` contains `com.apple.Preferences`; logs return ≥ 1 line within 5 s; crash list
  is a list; `screenshot` on the iPhone → exit 4 with the Mac reason; CLI e2e: `devices` lists the Android phone
  and the iPhone, and a command without `-d` exits 2.
- [ ] Full suite with both phones: 0 skipped. Measure `devices` latency with both phones vs Android only.
- [ ] README: iPhone section (what works from Windows, what needs a Mac and why). Fresh review; fix
  Critical/Important with failing-first tests.
