# WINPHONE

Windows-first, open-source control of **physical** Android phones (and, in later phases, iPhones) for
people and AI coding agents. One local service owns the phones; the `winphone` CLI (and later an MCP
server and a desktop app) talk to it.

Design: [docs/superpowers/specs/2026-11-16-winphone-design.md](docs/superpowers/specs/2026-11-16-winphone-design.md) ·
Phase 1 plan: [docs/superpowers/plans/2026-11-16-winphone-phase1.md](docs/superpowers/plans/2026-11-16-winphone-phase1.md)

## Status: Phase 1 (Android basics)

| Works now | Not built yet |
|---|---|
| `setup`, `doctor`, `devices`, `connect` / `disconnect` (USB and Wi-Fi), `capabilities`, `screenshot`, `view` (live scrcpy window), `daemon run/start/stop/status` | UI tree, tap/swipe/type, apps, logs, recording (Phase 2) · MCP server and permissions (Phase 3) · desktop app (Phase 4) · iPhone (Phases 5-6) |

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

`-d` takes a device id (`android:<serial>`) or a bare serial; with one phone online it can be omitted, with
two it is required. Add `--json` before the command for machine-readable output.

Exit codes: 0 ok · 1 error · 2 usage / more than one phone · 3 phone not found or not ready ·
4 capability unsupported · 5 permission denied · 6 outcome unknown · 7 service unavailable.

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
| Real-phone test suite (`WINPHONE_TEST_ANDROID` set) | 81 passed, 0 skipped |

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

## Known limitations (Phase 1)

- Android only; no input or UI inspection yet.
- Wi-Fi pairing and connect (`connect --pair/--wifi`) are covered by unit tests but have not yet been run
  against a real phone. USB was verified on a real phone (A069, Android 17).
- Any program running as your Windows user can read the service token (same-user processes cannot be
  separated on Windows without a separate account). The permission model in Phase 3 adds Windows Hello
  approvals for anything sensitive.
- Screenshots are not encrypted at rest; they are readable by your user like any other file you own.
- IPv6 addresses are not accepted for Wi-Fi pairing (Android shows an IPv4 address).

Licence: to be settled after the Phase 7 dependency review (MIT is the working assumption).
