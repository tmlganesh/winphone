from winphone_core.models import Capability, DeviceState, Platform, Transport, not_ready_hint
from winphone_ios.mapping import device_info_from_ios, ios_capabilities

UDID = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"
VALUES = {"ProductType": "iPhone10,6", "ProductVersion": "16.7.16"}


def test_iphone_x_model_name() -> None:
    d = device_info_from_ios(UDID, "USB", VALUES, trusted=True)
    assert (d.id, d.platform, d.state, d.transport) == (f"ios:{UDID}", Platform.IOS, DeviceState.ONLINE, Transport.USB)
    assert (d.model, d.os_version) == ("iPhone X", "16.7.16")


def test_unknown_model_keeps_product_type() -> None:
    assert device_info_from_ios(UDID, "Network", {"ProductType": "iPhone99,1"}, True).model == "iPhone99,1"
    assert device_info_from_ios(UDID, "Network", {}, True).transport is Transport.WIFI


def test_untrusted_iphone_is_unauthorized() -> None:
    d = device_info_from_ios(UDID, "USB", VALUES, trusted=False)
    assert d.state is DeviceState.UNAUTHORIZED
    assert "Trust" in (not_ready_hint(Platform.IOS, d.state) or "")
    assert "USB debugging" in (not_ready_hint(Platform.ANDROID, d.state) or "")


def test_ios_capabilities_honest() -> None:
    caps = {c.capability: c for c in ios_capabilities(DeviceState.ONLINE, go_ios_available=True)}
    assert {c for c, s in caps.items() if s.supported} == {Capability.LIST_APPS, Capability.LOGS,
                                                            Capability.CRASH_REPORTS}
    assert "WebDriverAgent" in (caps[Capability.TAP].reason or "")
    assert "developer disk image" in (caps[Capability.SCREENSHOT].reason or "")
    assert "developer disk image" in (caps[Capability.LAUNCH_APP].reason or "")


def test_ios_capabilities_without_go_ios() -> None:
    caps = {c.capability: c for c in ios_capabilities(DeviceState.ONLINE, go_ios_available=False)}
    assert not caps[Capability.LIST_APPS].supported and "winphone setup" in (caps[Capability.LIST_APPS].reason or "")


def test_untrusted_supports_nothing() -> None:
    assert not any(c.supported for c in ios_capabilities(DeviceState.UNAUTHORIZED, go_ios_available=True))
