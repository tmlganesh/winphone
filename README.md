# WINPHONE

**WINPHONE lets Claude Code and other AI agents see and control real Android phones from Windows, through an
MCP server and a CLI, with a human approving every sensitive action. iPhones are supported read-only from
Windows (apps, logs, crash reports).**

Open source (MIT) · Windows 10/11 · works with Claude Code and any MCP client · status: early (0.1.0)

```powershell
npm install -g winphone     # not published yet: install from source (below) for now
winphone setup              # pinned adb, scrcpy and go-ios, SHA-256 checked
winphone mcp install        # register with Claude Code, then start a new Claude Code session
```

Then ask Claude: *"Open Calculator on my phone and work out 12 × 7."* Claude reads the screen, asks you
once (a WINPHONE window on the laptop) before touching the phone, taps, and checks each result.

## Who it is for

- Developers on **Windows** who want an AI coding agent to test their Android app on a **real phone**.
- Anyone who wants to script or inspect an Android phone from Windows with a readable CLI.
- People who need to read an iPhone's apps, logs or crash reports from Windows without a Mac.

## What works today

| | Android (USB or Wi-Fi) | iPhone (from Windows only) |
|---|---|---|
| List phones, model, OS version, trust state | ✅ | ✅ |
| Read the screen as a compact element list (`observe`) | ✅ | needs a Mac |
| Tap, swipe, type, keys, launch app, each with a verified result | ✅ | needs a Mac |
| Screenshot, live view (scrcpy), screen recording (video only) | ✅ | needs a Mac |
| Install apps (always asks you) | ✅ | not yet |
| Device logs | ✅ | ✅ |
| Crash reports | needs root | ✅ |
| MCP server for Claude Code (15 tools) | ✅ | ✅ (what the iPhone supports) |

`winphone capabilities` reports exactly what a connected phone supports right now, with the reason for
every "no". Verified on a real Android phone (Android 17) and a real iPhone X (iOS 16.7.16).

## Using it from Claude Code (MCP)

`winphone mcp install` runs `claude mcp add --scope user winphone -- <winphone> mcp` and installs an agent
skill in `~/.claude/skills/winphone`. `winphone mcp uninstall` removes both. Tools:

`phone_devices` · `phone_connect` · `phone_disconnect` · `phone_capabilities` · `phone_observe` ·
`phone_screenshot` · `phone_act` · `phone_wait` · `phone_launch_app` · `phone_install_app` · `phone_record` ·
`phone_logs` · `phone_session` · `phone_authorize` · `phone_revoke`

An agent's loop: `phone_observe` → `phone_act` on a ref such as `e7` → read the receipt (`verified`,
`not_verified`, `unknown`, `failed`) → observe again. Observations are short text (Settings: about 1,000
tokens), not screenshots.

## Safety model

- **Agents may look; they need your OK to touch.** An agent calls `phone_authorize`; a WINPHONE window on the
  laptop asks you to allow tapping/typing on that phone for some minutes. No answer in 60 s means No.
- **Sensitive actions ask every time**, even with a grant: installs; anything while Settings, the Play Store,
  the installer or permission screens, messaging or payment apps are in front; and taps on things labelled
  Send, Pay, Buy, Delete, Transfer, Confirm, Uninstall, Reset (checked on the target, anything inside it and
  anything under the tapped point). Best effort, not a guarantee.
- **What you approved is what runs:** after you click Yes, WINPHONE looks again and sends nothing if the screen
  changed; an app file is copied before you are asked, and network paths are refused.
- **Commands typed in a real console count as you;** from scripts, CI or an agent's shell they get the agent
  rules. Only one approval window is shown at a time, and refused agents must wait before asking again.
- **Nothing is retried blindly:** an action whose delivery is uncertain is reported `unknown` with what the
  screen shows now.
- **Typed text is never logged**, shown in a window, or returned (only its length). Screen text is treated as
  data, never as instructions. Every decision is written to `%LOCALAPPDATA%\winphone\audit.jsonl`.

## Install from source

Requires Windows 10/11 and Python 3.11+.

```powershell
python -m pip install --user uv
python -m uv sync                   # pinned dependencies (uv.lock)
python -m uv run winphone setup     # adb 37.0.3, scrcpy 5.0.1, go-ios 1.3.2, each SHA-256 checked
python -m uv run winphone doctor
python -m uv run python packaging/build.py   # optional: dist\winphone\winphone.exe (no Python needed to run it)
```

Tools live in `%LOCALAPPDATA%\winphone\tools\`. Android Platform-Tools and Apple software are never bundled.

## Prepare the phone

**Android:** Settings > About phone > tap *Build number* 7 times; Developer options > *USB debugging* on; plug
in with a data cable, unlock, accept *Allow USB debugging*. Wi-Fi (Android 11+): Developer options > *Wireless
debugging* > *Pair device with pairing code*, then `winphone connect --pair IP:PAIRPORT --code CODE --wifi
IP:PORT` from a console.

**iPhone:** install Apple's **Apple Devices** app (Microsoft Store), plug the iPhone in, unlock it, tap *Trust*.

## Commands

| Command | What it does |
|---|---|
| `setup` · `doctor` | Install pinned tools · check everything, with a fix for each problem |
| `devices` · `capabilities` | List phones · what one can do now, with reasons |
| `connect` · `disconnect` | Hold a phone (and link it over Wi-Fi) · release it |
| `observe [--find TEXT]` | The screen as elements `e1 … eN` |
| `tap REF \| --text T \| --desc D \| --id RID \| --at X,Y` | Tap and verify |
| `swipe up\|down\|left\|right` · `type [TEXT] [--into REF]` · `press back\|home\|recents\|enter` | Input (omit TEXT for passwords: hidden prompt) |
| `apps [--all]` · `app launch PKG` · `app install FILE.apk` | Apps |
| `screenshot` · `view` · `record --seconds N` | Pixels: PNG, live window, MP4 |
| `logs [--grep T]` · `crashes` · `crashes pull NAME` | Diagnostics |
| `authorize grant\|list\|revoke` | Let agents interact with a phone for a while, or take it back |
| `mcp` · `mcp install\|status\|uninstall` | Serve MCP over stdio · manage the Claude Code registration |
| `daemon run\|start\|stop\|status` | The background service |

Add `--json` for machine-readable output and `-d <id>` when more than one phone is online. Exit codes: 0 ok ·
1 error / not verified · 2 usage or ambiguous · 3 phone not found / not ready · 4 unsupported · 5 permission
denied · 6 outcome unknown · 7 service unavailable.

## FAQ

**Can Claude Code control my Android phone on Windows?** Yes. Install WINPHONE, run `winphone mcp install`,
and start a new Claude Code session. Claude asks you once before it may tap or type.

**Is there something like terminal-phone for Windows?** WINPHONE is a Windows-first project in that space.
[terminal-phone](https://github.com/govijr/terminal-phone) targets macOS with the iOS Simulator and iPhones
through Xcode; WINPHONE targets Windows and real Android phones, and reads iPhones without a Mac. The table
below compares them as of 2026-11.

**Can it control an iPhone from Windows?** Not fully. Apple allows screenshots, launching and tapping only
through Xcode's developer tools on a Mac. From Windows alone WINPHONE reads apps, logs and crash reports.

**Is it safe to let an AI tap my phone?** See the safety model above: you approve access, sensitive actions
ask every time, and everything is audited. It cannot protect against a program already running as your
Windows user (see Known limitations).

**Which phones work?** Android phones with USB debugging (tested on Android 17); iPhones trusted by the PC
(tested on iOS 16.7).

| (2026-11) | WINPHONE | terminal-phone |
|---|---|---|
| Host OS | Windows 10/11 | macOS (Apple silicon) |
| Android | ✅ real phones | — |
| iPhone | read-only from Windows | Simulator and real iPhones (Xcode) |
| Interface | CLI + MCP server + local service | terminal viewer + CLI + MCP server |
| Licence | MIT | MIT |

## How it works

The first command that needs a phone starts `winphoned` in the background (no window). It listens on
`127.0.0.1` only, on a random port, and refuses requests without its token, with a foreign `Origin` or with
another `Host`. The token is in `%LOCALAPPDATA%\winphone\daemon.json`, readable by your user only. It exits
after 30 idle minutes. Android: adb, uiautomator2 (helper `/data/local/tmp/u2.jar`, stopped when you
disconnect) and scrcpy. iPhone: Apple Mobile Device Service and go-ios.

## Measured (laptop + Android 17 phone + iPhone X, 2026-11-16)

| | Result |
|---|---|
| `observe` (Settings, 107 elements) | 433 ms median; about 1,000 tokens |
| `tap` → verified receipt | 812 ms median |
| Screenshot (1224×2720) | 1.4–2.2 s |
| `winphone devices` from `winphone.exe`, service running | 1.5 s (3.5–4 s from Python) |
| MCP server start (`winphone.exe mcp`) | 6–10 s, once per session |
| iPhone discovery | 6 ms |
| Milestone: Claude-style MCP session opens Calculator, taps 7 by ref, verified | 30 s including your click |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unauthorized` | Unlock the phone and accept *Allow USB debugging*. No prompt? Developer options > *Revoke USB debugging authorisations*, replug. |
| `no phone found` | Use a data cable; try another port; some phones need the maker's USB driver. |
| Xiaomi / Oppo input refused | Xiaomi: *USB debugging (Security settings)*. Oppo/Realme: *Disable permission monitoring*. |
| Another adb (Android Studio) is running | Close it, or point it at `%LOCALAPPDATA%\winphone\tools\adb-37.0.3\platform-tools\adb.exe`. |
| An agent's taps are refused | Expected: ask it to call `phone_authorize`, or run `winphone authorize grant` in a console. |
| Service unavailable | Run `winphone daemon run` in a console to see the error. |

## Known limitations

- Any program running as your Windows user can read the service token, claim to be a console, or send
  keystrokes to the approval window. WINPHONE closes these paths in its own tools; it cannot separate
  programs of the same user (use a separate Windows account for untrusted agents).
- The sensitive-action rules are best effort, not a guarantee.
- iPhone: no screen, input or launching from Windows alone; no iPhone over Wi-Fi.
- Android 17: uiautomator2's device-info call is broken; WINPHONE reads the foreground app from `dumpsys`
  instead. Typing into a password field reports `not_verified` (Android hides its text).
- Wi-Fi pairing is unit-tested but not yet verified on a real phone.
- Screenshots and recordings are not encrypted at rest.

## Development

```powershell
python -m uv run pytest -q                                               # unit + security tests
$env:WINPHONE_TEST_ANDROID = "<serial>"; python -m uv run pytest -q     # plus real-phone tests
python -m uv run ruff check . ; python -m uv run mypy packages
```

Design: [docs/superpowers/specs/2026-11-16-winphone-design.md](docs/superpowers/specs/2026-11-16-winphone-design.md).
Plans per phase: [docs/superpowers/plans/](docs/superpowers/plans/).

## Licence

MIT, see [LICENSE](LICENSE). Third-party tools are downloaded by `winphone setup` under their own licences:
Android Platform-Tools (Google), scrcpy (Apache-2.0), go-ios (MIT).
