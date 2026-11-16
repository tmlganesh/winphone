# WINPHONE Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the owner's Android phone: read the screen as a compact list of elements with references, act on them (tap, swipe, type, keys, launch app), and get a receipt that says whether the effect was verified, through the daemon and the CLI.

**Architecture:** The Orchestrator gains `observe()` and `act()`. A pure observation module turns a UI-hierarchy XML into an `Observation` (elements `e1…eN`, a revision hash); an action module runs the lifecycle validate → resolve target → execute → wait → observe → verify → `Receipt`. `AndroidAdapter` gets UI and input methods. One lock per phone serialises observe/act.

**Tech Stack:** as Phase 1, plus `uiautomator2` 3.7.1 (already added to `winphone-android`).

**Spec:** `docs/superpowers/specs/2026-11-16-winphone-design.md` (§5.3, §6, §8, §9). Phase 1 plan and ledger: `docs/superpowers/plans/2026-11-16-winphone-phase1.md`, `.superpowers/sdd/2026-11-16-winphone-phase1/progress.md`.

## Facts measured on the owner's phone (A069, Android 17), 2026-11-16

- `uiautomator2` 3.7.1: `dump_hierarchy()` works (~200 ms) and `window_size()` works; `jsonrpc.deviceInfo` (so `d.info`) **fails** (`ApplicationSharedMemory not initialized`); `app_current()` took 10 s and returned the wrong app. Never call `info` or `app_current`.
- Built-in `adb exec-out uiautomator dump /dev/tty` works but takes 2.5–3.2 s.
- Foreground app: `dumpsys window` line `mCurrentFocus=Window{… u0 <pkg>/<activity>}`. Rotation: hierarchy root attribute `rotation` (0–3). Physical size: `wm size`.
- u2's `send_keys` goes through the phone clipboard and, on failure, silently installs an input-method APK. Neither is acceptable.

## Global Constraints

- All Phase 1 constraints still hold (argument lists, timeouts, no fakes outside `tests/`, `WINPHONE_HOME` in tests, token never printed).
- No commit, branch or push steps: tasks end "leave uncommitted"; the owner says when to commit.
- The only thing put on the phone is u2's `/data/local/tmp/u2.jar` (no APK, no IME). The helper is stopped when the phone is disconnected or the daemon exits.
- Typed text is never logged, never put in a receipt (only its length), never sent through the clipboard. Elements with `password="true"` carry no text.
- Screen text is untrusted data: it is stored and returned, never interpreted as an instruction or used to build a command.
- No blind retries: a failure after input was dispatched yields outcome `unknown` with a fresh observation, never a second attempt.
- Selectors that match more than one element are refused with the candidate refs; the engine never picks the first.
- Real-phone tests run only in the Settings app (navigate, scroll, search), never toggle anything, and end on the Home screen.
- New error codes: `stale_reference` (exit 1), `target_not_found` (exit 1), `target_ambiguous` (exit 2). Receipt outcomes map to exits: `verified` 0, `not_verified` 1, `failed` 1, `unknown` 6.
- Gates: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy packages`.
- Out of scope (Phase 3, with the permission tiers and MCP): install/uninstall, logs, recording, `winphone wait` as its own command.

## Review Focus

1. **Screen changed between observe and tap:** never tap the old coordinates; re-resolve once by (resource id, text, class) if exactly one match, else `stale_reference` with the new observation id. → Task 3 `test_reresolve_*`, Task 5 `test_tap_on_changed_screen_*`.
2. **Status-bar clock ticking:** must not count as "screen changed" (would make every action `verified`). → Task 2 `test_revision_ignores_system_ui`.
3. **Phone in landscape:** width/height swap; element centres stay correct. → Task 4 `test_display_size_rotated`.
4. **Two matching elements (two "OK" buttons):** `target_ambiguous` listing both refs. → Task 5 `test_selector_matching_two_is_ambiguous`.
5. **Typing into a password field:** the text appears in no receipt, observation, log or error. → Task 5 `test_typed_text_never_in_receipt`, Task 2 `test_secure_field_has_no_text`.

---

### Task 1: Input spike on the phone (owner-approved taps)

**Files:** scratchpad script only; results go to the ledger as `Task 1: Ruling:` lines.

- [ ] **Step 1:** With Settings open, measure on the phone: u2 `jsonrpc.click(x, y)` vs `adb shell input tap x y` (success + latency, 3 each, on a harmless Settings list entry, pressing Back after each); u2 `jsonrpc.swipe` vs `input swipe`; u2 `d(focused=True).set_text("battery")` in Settings search vs `input text battery`; `input keyevent 4/3/187`.
- [ ] **Step 2: Ruling** per action kind: use u2 where it works and is faster, else `adb shell input`. Text: u2 `set_text` if it works (Unicode); otherwise `input text` with TYPE's capability reason "ASCII only on this phone" and non-ASCII refused. Record the latencies.

---

### Task 2: Observation model, normaliser, compact format, find

**Files:** Create `packages/core/src/winphone_core/observation.py`; Test `tests/unit/test_observation.py` (hand-written XML fixtures, never a dump of the owner's screen).

**Interfaces (produces):**
- `Element(BaseModel)`: `ref: str, role: str, text: str | None, desc: str | None, resource_id: str | None, cls: str, package: str | None, bounds: tuple[int, int, int, int], clickable, scrollable, focusable, checkable, checked, enabled, selected, secure: bool`; property `center -> tuple[int, int]`.
- `Observation(BaseModel)`: `device_id, observation_id, revision: str, timestamp: datetime, width: int, height: int, rotation: int, foreground_app: str | None, elements: list[Element], screenshot_path: str | None = None`.
- `Selector(BaseModel)`: `text, text_contains, desc, resource_id, role: str | None; clickable: bool | None`; validator: at least one set.
- `normalize(xml: str, *, device_id: str, observation_id: str, foreground_app: str | None, width: int, height: int) -> Observation` (rotation from the root's `rotation` attribute, default 0).
- `find(obs: Observation, sel: Selector) -> list[Element]` (`resource_id` matches the full id or the part after `:id/`; `text` exact, `text_contains` case-insensitive).
- `to_compact(obs: Observation) -> str`: header `# app=<pkg> <w>x<h> rot=<r> rev=<revision> obs=<id> elements=<n>`, then one line per element: `e7 button "Sign in" id=login @540,1620 click` (flags in order: click, scroll, edit, checked/unchecked, disabled, secure).
- Rules: keep a node only if it has text, content-desc, resource-id, or is clickable/scrollable/checkable/editable; drop zero-area or fully off-screen nodes; refs `e1…` in document order; roles from class suffix: `Button/ImageButton→button, EditText→textbox, CheckBox→checkbox, Switch→switch, RadioButton→radio, ImageView→image, TextView→text, RecyclerView/ListView/ScrollView→list`, else `view`. **Revision** = first 12 hex of SHA-1 over (foreground app, width, height, rotation, and each element's role/text/desc/resource_id/bounds/flags) **excluding elements whose package is `com.android.systemui`**.

- [ ] **Step 1: Failing tests** — `test_layout_containers_dropped`, `test_refs_in_document_order`, `test_secure_field_has_no_text`, `test_revision_ignores_system_ui` (change a systemui clock text → same revision; change an app text → different), `test_find_by_resource_id_suffix`, `test_find_text_contains_case_insensitive`, `test_compact_line_format` (exact string for one fixture), `test_selector_requires_a_field`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with `xml.etree.ElementTree`. **Step 4:** Run → PASS. Leave uncommitted.

---

### Task 3: Observation store and re-resolution

**Files:** Modify `observation.py`, `packages/core/src/winphone_core/errors.py`; Test `tests/unit/test_observation_store.py`.

**Interfaces (produces):** `ObservationStore(keep: int = 8)`: `put(obs)`, `latest(device_id) -> Observation | None`, `get(device_id, observation_id) -> Observation | None`; `reresolve(old: Element, new: Observation) -> Element | None` (exactly one element with the same resource_id, text and cls, where at least one of resource_id/text is set). Errors `StaleReference`, `TargetNotFound`, `TargetAmbiguous` with the codes and exits in Global Constraints.

- [ ] **Step 1: Failing tests** — `test_store_keeps_last_n_per_device`, `test_reresolve_unique_match`, `test_reresolve_two_matches_is_none`, `test_reresolve_without_id_or_text_is_none`, `test_new_error_codes_roundtrip`.
- [ ] **Steps 2–4:** RED, implement, GREEN. Leave uncommitted.

---

### Task 4: Android UI and input

**Files:** Modify `packages/core/src/winphone_core/adapter.py`, `models.py`, `packages/adapters/android/src/winphone_android/{adapter,mapping}.py`, `tests/fakes.py`; Test `tests/unit/test_android_mapping.py`, `tests/unit/test_android_adapter.py`.

**Interfaces:**
- `models.py`: `Key(StrEnum)`: `BACK, HOME, RECENTS, ENTER`; `UiSnapshot` (dataclass): `xml: str, foreground_app: str | None, width: int, height: int`.
- `DeviceAdapter` adds: `ui_snapshot(serial) -> UiSnapshot`, `tap(serial, x, y) -> None`, `swipe(serial, x1, y1, x2, y2, duration_ms) -> None`, `type_text(serial, text) -> None` (into the focused field), `press(serial, key: Key) -> None`, `launch_app(serial, package) -> None`, `list_apps(serial, include_system: bool) -> list[str]`, `release(serial) -> None` (stop any on-phone helper; no-op if none). `Orchestrator.close()` releases every serial the adapters started a helper for; `runtime.serve()` calls it on exit. Contract: raise `AdapterError` when nothing reached the phone; raise `OutcomeUnknown` when the command may have been delivered (timeout, transport error after dispatch).
- `mapping.py` (pure): `parse_focus(dumpsys_window: str) -> str | None`; `display_size(physical: tuple[int, int], rotation: int) -> tuple[int, int]`; `PACKAGE_RE = r"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$"`; `KEYCODES = {BACK: 4, HOME: 3, RECENTS: 187, ENTER: 66}`. `android_capabilities` now marks OBSERVE_TREE, TAP, SWIPE, TYPE, KEYS, LAUNCH_APP, LIST_APPS supported when ONLINE (TYPE with the Task 1 reason if ASCII-only); INSTALL_APP, LOGS, RECORD reason `"Not built yet (Phase 3)"`.
- `AndroidAdapter`: one lazily created `uiautomator2.Device` per serial (guarded by a lock); `ui_snapshot` = u2 `dump_hierarchy()`, falling back to the built-in dump on any u2 error; physical size from `wm size` cached per serial; input per the Task 1 rulings; `launch_app` validates `PACKAGE_RE` then `monkey -p <pkg> -c android.intent.category.LAUNCHER 1`; `list_apps` = `pm list packages` (`-3` unless `include_system`); `release(serial)` = u2 `stop_uiautomator()` and forget the device object. A `SecurityException` mentioning `INJECT_EVENTS` marks TAP/SWIPE/TYPE/KEYS unsupported for that serial with the vendor-toggle reason ("Xiaomi: Developer options > USB debugging (Security settings)").

- [ ] **Step 1: Failing tests** — `test_parse_focus` (sample `mCurrentFocus` line → package; no line → None), `test_display_size_rotated` ((1224, 2720), 1 → (2720, 1224)), `test_launch_rejects_bad_package` (`"-x"`, `"a;b"`), `test_capabilities_phase2_supported`, `test_snapshot_falls_back_to_builtin_dump` (fake u2 raising), `test_inject_events_denial_marks_input_unsupported`.
- [ ] **Steps 2–4:** RED, implement, GREEN. Update `FakeAdapter` with the new methods (records calls; a scripted list of `UiSnapshot`s returned in order). Leave uncommitted.

---

### Task 5: Actions, waits, receipts

**Files:** Create `packages/core/src/winphone_core/actions.py`; Modify `orchestrator.py`; Test `tests/unit/test_actions.py`.

**Interfaces:**
- `Target(BaseModel)`: exactly one of `ref: str` (+ optional `observation_id`, default: latest for the device), `selector: Selector`, or `point: tuple[int, int]`.
- `Action(BaseModel)`: `kind: Literal["tap", "swipe", "type", "press", "launch"]`, `target: Target | None`, `direction: Literal["up", "down", "left", "right"] | None`, `to: tuple[int, int] | None`, `text: str | None` (`repr=False`), `key: Key | None`, `package: str | None`; validator per kind.
- `Wait(BaseModel)`: one of `text_visible`, `text_gone`, `element: Selector`, `app_foreground: str`, `revision_changed: bool`; `timeout_s: float = 10` (0 < t ≤ 60).
- `Receipt(BaseModel)`: `action_id, device_id, kind, target_ref: str | None, resolved_by: Literal["ref", "reresolved", "selector", "point"] | None, before_revision, after_revision: str, outcome: Literal["verified", "not_verified", "unknown", "failed"], evidence: str, duration_ms: int, observation_id: str`.
- `Orchestrator.observe(device_id, screenshot: bool = False) -> Observation` and `Orchestrator.act(device_id, action: Action, wait: Wait | None = None, idempotency_key: str | None = None) -> Receipt`, both under a per-device lock; `disconnect` also calls the adapter's `release`.
- Lifecycle in `actions.run(...)`: ready + capability (TAP/SWIPE/TYPE/KEYS/LAUNCH_APP by kind) → fresh observation → resolve target (ref: same revision → use it; different → `reresolve` or `StaleReference`; selector: exactly one or `TargetNotFound`/`TargetAmbiguous`) → execute (tap = element centre; swipe direction = 25%↔75% of the element's bounds or the screen, 300 ms; type = tap the target first if given, then `type_text`) → wait (poll `observe` every 300 ms; default `revision_changed` for 3 s) → receipt. `OutcomeUnknown` from the adapter → observe and return outcome `unknown` with what is on screen. Idempotency: last 128 receipts per daemon keyed by `idempotency_key`; a repeat returns the stored receipt without acting.

- [ ] **Step 1: Failing tests** (FakeAdapter with scripted snapshots) — `test_tap_by_ref_verified_when_screen_changes`, `test_tap_not_verified_when_screen_unchanged`, `test_tap_on_changed_screen_reresolves_unique`, `test_tap_on_changed_screen_without_match_is_stale_and_does_not_tap`, `test_selector_matching_two_is_ambiguous`, `test_wait_text_visible_times_out_not_verified`, `test_outcome_unknown_is_never_retried` (adapter called once), `test_idempotency_key_returns_same_receipt`, `test_typed_text_never_in_receipt` (secure field; `"hunter2"` absent from `receipt.model_dump_json()`), `test_capability_checked_before_acting`, `test_launch_waits_for_app_foreground`.
- [ ] **Steps 2–4:** RED, implement, GREEN. Leave uncommitted.

---

### Task 6: Daemon routes and client

**Files:** Modify `server.py`, `client.py`; Test `tests/security/test_daemon_auth.py`, `tests/unit/test_client.py`.

**Interfaces:** `POST /devices/{id}/observe {screenshot: bool}` → `Observation`; `POST /devices/{id}/act {action, wait, idempotency_key}` → `Receipt`; `GET /devices/{id}/apps?all=bool` → `list[str]`. Client: `observe(device_id, screenshot=False) -> Observation`, `act(device_id, action, wait=None, idempotency_key=None) -> Receipt`, `apps(device_id, include_system=False) -> list[str]`.

- [ ] **Step 1: Failing tests** — `test_act_route_returns_receipt`, `test_act_route_needs_token`, `test_type_text_not_echoed_in_validation_error` (invalid body containing text → 422 body without the text), client round-trip via `sender_for`.
- [ ] **Steps 2–4:** RED, implement, GREEN. Leave uncommitted.

---

### Task 7: CLI

**Files:** Modify `packages/cli/src/winphone_cli/main.py`; Test `tests/unit/test_cli.py`.

**Commands:** `observe [-d] [--find TEXT]` (compact text; `--json` full) · `tap [REF] [--text T | --desc D | --id RID | --at X,Y]` · `swipe up|down|left|right [--on REF]` or `swipe --from X,Y --to X,Y` · `type TEXT [--into REF]` · `press back|home|recents|enter` · `apps [--all]` · `app launch PKG`. Action commands take `--wait-text`, `--wait-gone`, `--wait-app`, `--wait-timeout`; exit by outcome (Global Constraints). Human output: one line `verified: <evidence> (obs o12, 640 ms)`.

- [ ] **Step 1: Failing tests** — `test_observe_prints_compact`, `test_tap_by_text_exit_0_when_verified`, `test_tap_not_verified_exit_1`, `test_type_text_not_printed`, `test_press_rejects_unknown_key` (exit 2), `test_app_launch_bad_package_exit_2`.
- [ ] **Steps 2–4:** RED, implement, GREEN. Leave uncommitted.

---

### Task 8: Real-phone sign-off, docs, review

**Files:** Create `tests/android/test_phase2_device.py`; Modify `README.md`.

- [ ] **Step 1:** Real-phone tests (marked `android`): launch `com.android.settings` → `app_foreground` verified; `observe` has elements and its compact form fits in 6 KB; swipe up until an element with text "About phone" is visible (≤ 6 swipes), tap it by selector → `verified`; press Back → `verified`; stale ref: observe, press Back, tap the old ref → `stale_reference` or `reresolved`, never a blind tap; Settings search: tap the search element, type "battery", wait `text_visible` "Battery" → `verified`; press Home → foreground is not Settings.
- [ ] **Step 2:** `WINPHONE_TEST_ANDROID=<serial> uv run pytest -q` → all pass, 0 skipped.
- [ ] **Step 3:** Measure and record in the README: `observe` latency (daemon and CLI), tap → verified receipt latency, compact observation size in characters for the Settings screen.
- [ ] **Step 4:** README: Phase 2 commands, what goes on the phone (`u2.jar`), the typing rules, known limitations. Then the fresh-reviewer pass. Leave uncommitted.
