"""Pure translation from adb's view of a phone to WINPHONE's models."""

from __future__ import annotations

import re
from collections.abc import Mapping

from winphone_core.models import (
    NOT_READY_HINTS,
    PACKAGE_PATTERN,
    Capability,
    CapabilityStatus,
    DeviceInfo,
    DeviceState,
    Key,
    Platform,
    Transport,
)

PROP_KEYS = ("ro.product.model", "ro.build.version.release", "ro.build.version.sdk")
KEYCODES = {Key.BACK: 4, Key.HOME: 3, Key.RECENTS: 187, Key.ENTER: 66}
PACKAGE_RE = re.compile(PACKAGE_PATTERN, re.ASCII)  # use fullmatch: '$' alone accepts a trailing newline
INPUT_DENIED_REASON = ("The phone blocks simulated input. Xiaomi/Redmi/POCO: Developer options > "
                       "USB debugging (Security settings); Oppo/Realme: Disable permission monitoring")

_STATES = {"device": DeviceState.ONLINE, "unauthorized": DeviceState.UNAUTHORIZED}
_BUILT = {Capability.SCREENSHOT, Capability.LIVE_VIEW, Capability.WIRELESS, Capability.OBSERVE_TREE,
          Capability.TAP, Capability.SWIPE, Capability.TYPE, Capability.KEYS, Capability.LAUNCH_APP,
          Capability.LIST_APPS, Capability.LOGS, Capability.INSTALL_APP, Capability.RECORD}
CRASH_REPORTS_REASON = "Android keeps crash reports where only root can read them; use winphone logs"
_INPUT = {Capability.TAP, Capability.SWIPE, Capability.TYPE, Capability.KEYS}


def parse_focus(dumpsys_window: str) -> str | None:
    """Package of the focused window, from `dumpsys window`'s mCurrentFocus line."""
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([^/}\s]+)", dumpsys_window)
    return m.group(1) if m else None


def parse_wm_size(out: str) -> tuple[int, int]:
    sizes = dict(re.findall(r"(Physical|Override) size: (\d+x\d+)", out))
    w, h = (sizes.get("Override") or sizes["Physical"]).split("x")
    return int(w), int(h)


def display_size(physical: tuple[int, int], rotation: int) -> tuple[int, int]:
    w, h = physical
    return (h, w) if rotation in (1, 3) else (w, h)


def device_info_from(serial: str, adb_state: str, props: Mapping[str, str]) -> DeviceInfo:
    # Network serials are "host:port" (adb connect) or "adb-<id>._adb-tls-connect._tcp" (mDNS).
    wifi = ":" in serial or serial.startswith("adb-")
    sdk = props.get("ro.build.version.sdk", "")
    return DeviceInfo(
        id=f"android:{serial}",
        platform=Platform.ANDROID,
        serial=serial,
        state=_STATES.get(adb_state, DeviceState.OFFLINE),
        transport=Transport.WIFI if wifi else Transport.USB,
        model=props.get("ro.product.model") or None,
        os_version=props.get("ro.build.version.release") or None,
        sdk=int(sdk) if sdk.isdigit() else None,
    )


def android_capabilities(state: DeviceState, sdk: int | None, scrcpy_available: bool,
                         input_denied: bool = False) -> list[CapabilityStatus]:
    if state is not DeviceState.ONLINE:
        reason = NOT_READY_HINTS[state]
        return [CapabilityStatus(capability=c, supported=False, reason=reason) for c in Capability]
    out: list[CapabilityStatus] = []
    for c in Capability:
        if c is Capability.CRASH_REPORTS:
            out.append(CapabilityStatus(capability=c, supported=False, reason=CRASH_REPORTS_REASON))
        elif c not in _BUILT:
            out.append(CapabilityStatus(capability=c, supported=False, reason="Not built yet (Phase 3)"))
        elif c in _INPUT and input_denied:
            out.append(CapabilityStatus(capability=c, supported=False, reason=INPUT_DENIED_REASON))
        elif c in (Capability.LIVE_VIEW, Capability.RECORD) and not scrcpy_available:
            out.append(CapabilityStatus(capability=c, supported=False,
                                        reason="scrcpy not installed: run winphone setup"))
        elif c is Capability.WIRELESS:
            how = "adb pair (Android 11+)" if (sdk or 0) >= 30 else "adb tcpip over USB first, then connect"
            out.append(CapabilityStatus(capability=c, supported=True, reason=how))
        else:
            out.append(CapabilityStatus(capability=c, supported=True))
    return out
