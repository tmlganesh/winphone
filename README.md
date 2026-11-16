# WINPHONE

Windows-first, open-source control of **physical** Android phones (and, in later phases, iPhones) for
people and AI coding agents. One local service owns the phones; the `winphone` CLI (and later an MCP
server and a desktop app) talk to it.

Design: [docs/superpowers/specs/2026-11-16-winphone-design.md](docs/superpowers/specs/2026-11-16-winphone-design.md) ·
Phase 1 plan: [docs/superpowers/plans/2026-11-16-winphone-phase1.md](docs/superpowers/plans/2026-11-16-winphone-phase1.md)

## Status: Phase 2 (Android: read the screen and act on it)

| Works now | Not built yet |
|---|---|
| `setup`, `doctor`, `devices`, `connect` / `disconnect` (USB and Wi-Fi), `capabilities`, `screenshot`, `view` (live scrcpy window), `daemon run/start/stop/status` · **Phase 2:** `observe`, `tap`, `swipe`, `type`, `press`, `apps`, `app launch`, each with a verified/not-verified receipt | install, logs, recording, MCP server and permissions (Phase 3) · desktop app (Phase 4) · iPhone (Phases 5-6) |

`winphone capabilities` reports exactly what the connected phone supports right now, with the reason for
everything it does not.

## Install

Requires Windows 10/11 and Python 3.11+.

```powershell
python -m pip install --user uv
python -m uv sync                 # creates .venv with pinned dependencies (uv.lock)
python -m uv run winphone setup   # downloads pinned adb 37.0.3 and scrcpy 5.0.1, SHA-256 checked
python -m uv run winphone doctor
```

`setup` refuses to install anything whose checksum does not match the pin. Tools live in
`%LOCALAPPDATA%\winphone\tools\`; Android Platform-Tools are never bundled (their licence forbids it).

## Prepare the Android phone

1. Settings > About phone > tap **Build number** 7 times (Developer options appear).
2. Developer options > turn on **USB debugging**.
3. Plug in with a **data** cable, unlock the phone and accept **Allow USB debugging** (tick *Always allow*).
4. `winphone devices` should show it as `online`.

Wi-Fi (Android 11+): Developer options > **Wireless debugging** > *Pair device with pairing code*, then

```powershell
winphone connect --pair 192.168.1.20:37123 --code 123456 --wifi 192.168.1.20:41005
```

(the pairing port and the connect port shown on the phone are different).

## Commands

| Command | What it does |
|---|---|
| `winphone setup [--force]` | Install pinned adb + scrcpy |
| `winphone doctor` | Check tools, service, phones, and later-phase prerequisites; exit 1 on any failure |
| `winphone devices` | List phones adb can see |
| `winphone connect [-d ID] [--wifi H:P] [--pair H:P --code C]` | Hold a phone (link it over Wi-Fi first if asked) |
| `winphone disconnect [-d ID]` | Release it (and drop the Wi-Fi link) |
| `winphone capabilities [-d ID]` | What it can do now, with reasons |
| `winphone screenshot [-d ID] [-o FILE]` | PNG of the screen |
| `winphone view [-d ID]` | Live screen in a scrcpy window (mouse + keyboard work there) |
| `winphone daemon run/start/stop/status` | Control the background service |
| `winphone observe [--find TEXT] [--screenshot]` | The screen as a compact element list (see below) |
| `winphone tap [REF \| --text T \| --desc D \| --id RID \| --at X,Y]` | Tap, then verify the screen responded |
| `winphone swipe up\|down\|left\|right [--on REF]` · `swipe --from X,Y --to X,Y` | Swipe (content moves the other way) |
| `winphone type [TEXT] [--into REF]` | Set the focused field's text (Unicode; never printed or logged). Omit TEXT for passwords: you get a hidden prompt (or it is read from stdin), so it never lands in shell history |
| `winphone press back\|home\|recents\|enter` | System keys |
| `winphone apps [--all]` · `winphone app launch PKG` | List packages · launch and wait until it is in front |

`-d` takes a device id (`android:<serial>`) or a bare serial; with one phone online it can be omitted, with
two it is required. Add `--json` before the command for machine-readable output. Action commands take
`--wait-text`, `--wait-gone`, `--wait-app` or `--wait-timeout`; without them an action is verified when the
screen changes within 3 s (launch: when the app is in front within 10 s).

Exit codes: 0 ok / verified · 1 error / not verified / failed / stale reference · 2 usage, more than one phone,
or a selector matching several elements · 3 phone not found or not ready · 4 capability unsupported ·
5 permission denied · 6 outcome unknown · 7 service unavailable.

## Reading and acting on the screen

```text
$ winphone observe
# app=com.android.settings 1224x2720 rot=0 rev=89462f829da4 obs=o27 elements=107
e12 text "Display" id=title @282,425
e107 text "About phone" id=title @349,2567
$ winphone tap --text "About phone"
verified: screen changed (e107, obs o29, 812 ms)
```

- `e12` refers to the phone's latest observation. If the screen changed since, the element is found again
  only if exactly one element has the same id, text and class; otherwise the command fails with
  `stale_reference` and never taps where the element used to be.
- A selector that matches several elements is refused with their refs; WINPHONE never picks one.
- An action whose delivery is uncertain is reported `unknown` with what the screen shows now. It is never
  repeated automatically.
- Typing sets the field's text directly (Unicode works). It never goes through the phone's clipboard and
  never installs a keyboard app; the text appears in no receipt, log or error, only its length.
- Screen text is data: WINPHONE stores and returns it, never acts on what it says.
- What goes on the phone: uiautomator2's helper `/data/local/tmp/u2.jar` (no app is installed). It is
  stopped when you `disconnect` or the service exits.

## How it works

The first command that needs a phone starts `winphoned` in the background. It listens on
`127.0.0.1` only, on a random port, and refuses any request without its token, with a foreign `Origin`,
or with a `Host` other than `127.0.0.1:<port>`. The port and token are in
`%LOCALAPPDATA%\winphone\daemon.json`, which is locked down to your Windows user. The service survives the
terminal or Claude Code closing and exits after 30 idle minutes. Screenshots are kept under
`%LOCALAPPDATA%\winphone\artifacts\` (last 50 per phone).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unauthorized` | Unlock the phone and accept *Allow USB debugging*. If no prompt: Developer options > *Revoke USB debugging authorisations*, replug. |
| `no phone found` | Use a data cable (many are charge-only); try another port; some phones need the maker's USB driver (Samsung, older Xiaomi). |
| `offline` | Replug, or toggle USB debugging off and on. |
| Xiaomi / Redmi / POCO | Also enable *USB debugging (Security settings)*; Oppo/Realme: *Disable permission monitoring*. Needed for input in Phase 2. |
| Android Studio or another adb is running | Different adb versions restart each other's server. Close the other tool, or point it at `%LOCALAPPDATA%\winphone\tools\adb-37.0.3\platform-tools\adb.exe`. |
| A command says the service is unavailable | Run `winphone daemon run` in a terminal to see the error in the foreground. |
| Black areas in screenshots or the live view | The app marks that screen secure (`FLAG_SECURE`); Android blanks it by design. |

## Measured on the development laptop

Phone: model A069, Android 17, over USB (2026-11-16).

| Measurement | Result |
|---|---|
| Service answers `/devices` | 16 ms |
| Screenshot through the service (1224×2720 PNG, encoded on the phone) | 1.4–2.2 s |
| `winphone screenshot`, service already running | 5.1 s (adds Python start-up and imports below) |
| `winphone devices`, service already running | 3.5–4.0 s (Python start-up alone is 1.4 s on this machine; imports 2.1 s, Pydantic 0.95 s) |
| First command, including starting the service | 12.8 s |
| `observe` through the service (Settings, 107 elements) | 433 ms median (362–585) |
| Compact observation of Settings | 3,953 characters (about 1,000 tokens) |
| `tap` → verified receipt (includes observing before and after) | 812 ms median |
| `press back` → verified receipt | 1.48 s median (waits out the page transition) |
| `winphone observe` from the CLI | 2.6–3.5 s (Python start-up dominates) |
| Full test suite with the real-phone tests | 151 passed, 0 skipped |

The CLI pays Python start-up on every call; the MCP server (Phase 3) is a long-running process and pays it
once.

## Development

```powershell
python -m uv run pytest -q                                   # unit + security tests
$env:WINPHONE_TEST_ANDROID = "<serial>"; python -m uv run pytest -q   # also the real-phone tests
python -m uv run ruff check .
python -m uv run mypy packages
```

Real-phone tests only list, probe, take screenshots and open the viewer: no taps, typing or installs.

## Known limitations

- Android only. Install, logs and recording come in Phase 3 with the permission model.
- On Android 17, uiautomator2 3.7.1's device-info call is broken; WINPHONE never uses it and reads the
  foreground app and rotation from `dumpsys` instead. If the helper fails, the screen is read with Android's
  own dump (2.5–3.2 s instead of ~0.4 s).
- `--wait-text` matches a substring of an element's text or description.
- A phone does one thing at a time; requests for the same phone wait for each other.
- Typing into a password field is reported `not_verified` unless something else on screen changes:
  Android hides the field's text, so WINPHONE cannot see that it changed.
- `app launch` resumes an app where it was left, as tapping its icon does.
- uiautomator2 itself re-sends a call after some HTTP errors; in rare cases a partly delivered swipe could
  be repeated by it. WINPHONE never retries on its own.
- `swipe up --from X,Y` ignores `--from` (a direction swipes the whole screen, or `--on REF`).
- Wi-Fi pairing and connect (`connect --pair/--wifi`) are covered by unit tests but have not yet been run
  against a real phone. USB was verified on a real phone (A069, Android 17).
- Any program running as your Windows user can read the service token (same-user processes cannot be
  separated on Windows without a separate account). The permission model in Phase 3 adds Windows Hello
  approvals for anything sensitive.
- Screenshots are not encrypted at rest; they are readable by your user like any other file you own.
- IPv6 addresses are not accepted for Wi-Fi pairing (Android shows an IPv4 address).

Licence: to be settled after the Phase 7 dependency review (MIT is the working assumption).
