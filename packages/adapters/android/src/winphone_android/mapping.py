"""Pure translation from adb's view of a phone to WINPHONE's models."""

from __future__ import annotations

from collections.abc import Mapping

from winphone_core.models import (
    NOT_READY_HINTS,
    Capability,
    CapabilityStatus,
    DeviceInfo,
    DeviceState,
    Platform,
    Transport,
)

PROP_KEYS = ("ro.product.model", "ro.build.version.release", "ro.build.version.sdk")

_STATES = {"device": DeviceState.ONLINE, "unauthorized": DeviceState.UNAUTHORIZED}
_BUILT_IN_PHASE_1 = {Capability.SCREENSHOT, Capability.LIVE_VIEW, Capability.WIRELESS}


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


def android_capabilities(state: DeviceState, sdk: int | None,
                         scrcpy_available: bool) -> list[CapabilityStatus]:
    if state is not DeviceState.ONLINE:
        reason = NOT_READY_HINTS[state]
        return [CapabilityStatus(capability=c, supported=False, reason=reason) for c in Capability]
    out: list[CapabilityStatus] = []
    for c in Capability:
        if c not in _BUILT_IN_PHASE_1:
            out.append(CapabilityStatus(capability=c, supported=False, reason="Not built yet (Phase 2)"))
        elif c is Capability.LIVE_VIEW and not scrcpy_available:
            out.append(CapabilityStatus(capability=c, supported=False,
                                        reason="scrcpy not installed: run winphone setup"))
        elif c is Capability.WIRELESS:
            how = "adb pair (Android 11+)" if (sdk or 0) >= 30 else "adb tcpip over USB first, then connect"
            out.append(CapabilityStatus(capability=c, supported=True, reason=how))
        else:
            out.append(CapabilityStatus(capability=c, supported=True))
    return out
