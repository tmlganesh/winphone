"""Pure translation from what Windows can learn about an iPhone to WINPHONE's models."""

from __future__ import annotations

from collections.abc import Mapping

from winphone_core.models import (
    Capability,
    CapabilityStatus,
    DeviceInfo,
    DeviceState,
    Platform,
    Transport,
    not_ready_hint,
)

_MODELS = {
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X", "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR", "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE (2nd generation)",
}
# Apple reserves these for its developer tools: say so instead of pretending.
_NEEDS_DDI = "needs Apple's developer disk image from Xcode on a Mac, with Developer Mode on"
_NEEDS_WDA = "needs WebDriverAgent built and signed on a Mac (Phase 6)"
REASONS = {
    Capability.OBSERVE_TREE: _NEEDS_WDA, Capability.TAP: _NEEDS_WDA, Capability.SWIPE: _NEEDS_WDA,
    Capability.TYPE: _NEEDS_WDA, Capability.KEYS: _NEEDS_WDA, Capability.SCREENSHOT: _NEEDS_DDI,
    Capability.LAUNCH_APP: _NEEDS_DDI, Capability.RECORD: _NEEDS_DDI,
    Capability.LIVE_VIEW: "no live view for iPhone from Windows (AirPlay mirroring would need a separate app)",
    Capability.WIRELESS: "iPhone over Wi-Fi is not supported yet",
    Capability.INSTALL_APP: "Not built yet (Phase 3)",
}
GO_IOS_CAPABILITIES = {Capability.LIST_APPS, Capability.LOGS, Capability.CRASH_REPORTS}


def device_info_from_ios(udid: str, connection: str, values: Mapping[str, str], trusted: bool) -> DeviceInfo:
    product = values.get("ProductType")
    return DeviceInfo(
        id=f"ios:{udid}",
        platform=Platform.IOS,
        serial=udid,
        state=DeviceState.ONLINE if trusted else DeviceState.UNAUTHORIZED,
        transport=Transport.USB if connection == "USB" else Transport.WIFI,
        model=_MODELS.get(product, product) if product else None,
        os_version=values.get("ProductVersion"),
    )


def ios_capabilities(state: DeviceState, go_ios_available: bool) -> list[CapabilityStatus]:
    if state is not DeviceState.ONLINE:
        hint = not_ready_hint(Platform.IOS, state)
        return [CapabilityStatus(capability=c, supported=False, reason=hint) for c in Capability]
    out = []
    for c in Capability:
        if c in GO_IOS_CAPABILITIES:
            reason = None if go_ios_available else "go-ios not installed: run winphone setup"
            out.append(CapabilityStatus(capability=c, supported=go_ios_available, reason=reason))
        else:
            out.append(CapabilityStatus(capability=c, supported=False, reason=REASONS[c]))
    return out
