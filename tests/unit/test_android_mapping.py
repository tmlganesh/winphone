from winphone_android.mapping import android_capabilities, device_info_from
from winphone_core.models import Capability, DeviceState, Transport


def test_device_info_unauthorized() -> None:
    d = device_info_from("R58M", "unauthorized", {})
    assert d.state is DeviceState.UNAUTHORIZED
    assert d.transport is Transport.USB
    assert d.id == "android:R58M"
    assert d.model is None and d.sdk is None


def test_device_info_wifi_and_props() -> None:
    d = device_info_from("192.168.1.5:5555", "device",
                         {"ro.product.model": "Pixel 7", "ro.build.version.sdk": "34",
                          "ro.build.version.release": "14"})
    assert d.state is DeviceState.ONLINE
    assert d.transport is Transport.WIFI
    assert (d.model, d.sdk, d.os_version) == ("Pixel 7", 34, "14")


def test_device_info_mdns_serial_is_wifi() -> None:
    assert device_info_from("adb-R58M-x._adb-tls-connect._tcp", "device", {}).transport is Transport.WIFI


def test_device_info_offline_and_unknown_states() -> None:
    assert device_info_from("A", "offline", {}).state is DeviceState.OFFLINE
    assert device_info_from("A", "recovery", {}).state is DeviceState.OFFLINE


def test_capabilities_offline_device_supports_nothing() -> None:
    assert not any(c.supported for c in android_capabilities(DeviceState.OFFLINE, None, True))


def test_capabilities_cover_every_capability_once() -> None:
    caps = [c.capability for c in android_capabilities(DeviceState.ONLINE, 34, True)]
    assert sorted(caps) == sorted(Capability)


def test_capabilities_phase2_items_say_so() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, True)}
    assert c[Capability.SCREENSHOT].supported
    assert c[Capability.WIRELESS].supported
    assert not c[Capability.TAP].supported
    assert c[Capability.TAP].reason == "Not built yet (Phase 2)"


def test_live_view_unsupported_without_scrcpy() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, False)}
    assert not c[Capability.LIVE_VIEW].supported
    assert "winphone setup" in (c[Capability.LIVE_VIEW].reason or "")


def test_unauthorized_reason_names_the_fix() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.UNAUTHORIZED, None, True)}
    assert "Allow USB debugging" in (c[Capability.SCREENSHOT].reason or "")
