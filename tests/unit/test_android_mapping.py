from winphone_android.mapping import (
    PACKAGE_RE,
    android_capabilities,
    device_info_from,
    display_size,
    parse_focus,
    parse_wm_size,
)
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


def test_capabilities_phase2_supported() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, True)}
    for cap in (Capability.SCREENSHOT, Capability.WIRELESS, Capability.OBSERVE_TREE, Capability.TAP,
                Capability.SWIPE, Capability.TYPE, Capability.KEYS, Capability.LAUNCH_APP, Capability.LIST_APPS):
        assert c[cap].supported, cap
    for cap in (Capability.INSTALL_APP, Capability.LOGS, Capability.RECORD):
        assert not c[cap].supported and c[cap].reason == "Not built yet (Phase 3)"


def test_input_denied_marks_input_unsupported_with_vendor_hint() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, True, input_denied=True)}
    for cap in (Capability.TAP, Capability.SWIPE, Capability.TYPE, Capability.KEYS):
        assert not c[cap].supported and "USB debugging (Security settings)" in (c[cap].reason or "")
    assert c[Capability.OBSERVE_TREE].supported


def test_parse_focus() -> None:
    out = "  mCurrentFocus=Window{e8328df u0 com.nothing.launcher/com.android.searchlauncher.SearchLauncher}\n"
    assert parse_focus(out) == "com.nothing.launcher"
    assert parse_focus("mCurrentFocus=null\n") is None
    assert parse_focus("") is None


def test_parse_wm_size_prefers_override() -> None:
    assert parse_wm_size("Physical size: 1224x2720\n") == (1224, 2720)
    assert parse_wm_size("Physical size: 1224x2720\nOverride size: 1080x2400\n") == (1080, 2400)


def test_display_size_rotated() -> None:
    assert display_size((1224, 2720), 0) == (1224, 2720)
    assert display_size((1224, 2720), 1) == (2720, 1224)
    assert display_size((1224, 2720), 3) == (2720, 1224)


def test_package_name_rule() -> None:
    assert PACKAGE_RE.match("com.android.settings")
    for bad in ("-x", "a;b", "settings", "com.x y", "com..x"):
        assert not PACKAGE_RE.match(bad), bad


def test_live_view_unsupported_without_scrcpy() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.ONLINE, 34, False)}
    assert not c[Capability.LIVE_VIEW].supported
    assert "winphone setup" in (c[Capability.LIVE_VIEW].reason or "")


def test_unauthorized_reason_names_the_fix() -> None:
    c = {x.capability: x for x in android_capabilities(DeviceState.UNAUTHORIZED, None, True)}
    assert "Allow USB debugging" in (c[Capability.SCREENSHOT].reason or "")
