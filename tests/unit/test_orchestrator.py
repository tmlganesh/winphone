from pathlib import Path

import pytest
from fakes import FakeAdapter, dev
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import CapabilityUnsupported, DeviceAmbiguous, DeviceNotFound, DeviceNotReady
from winphone_core.models import Capability, DeviceState, Transport
from winphone_core.orchestrator import Orchestrator


def orch(fake: FakeAdapter, tmp_path: Path) -> Orchestrator:
    return Orchestrator([fake], ArtifactStore(tmp_path))


def test_resolve_single_online_device_auto_selects(tmp_path: Path) -> None:
    fake = FakeAdapter([dev("A"), dev("B", DeviceState.UNAUTHORIZED)])
    assert orch(fake, tmp_path).resolve(None).id == "android:A"


def test_resolve_two_online_devices_is_ambiguous(tmp_path: Path) -> None:
    with pytest.raises(DeviceAmbiguous) as e:
        orch(FakeAdapter([dev("A"), dev("B")]), tmp_path).resolve(None)
    assert "android:A" in str(e.value) and "android:B" in str(e.value)


def test_resolve_with_no_devices_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(DeviceNotFound):
        orch(FakeAdapter(), tmp_path).resolve(None)


def test_resolve_only_unauthorized_phone_gives_usb_hint(tmp_path: Path) -> None:
    """Review finding 3: this used to say 'no phone is online / run doctor' with no fix."""
    with pytest.raises(DeviceNotReady) as e:
        orch(FakeAdapter([dev("A", DeviceState.UNAUTHORIZED)]), tmp_path).resolve(None)
    assert "android:A" in str(e.value)
    assert "Allow USB debugging" in (e.value.hint or "")


def test_held_phone_that_vanishes_is_released(tmp_path: Path) -> None:
    """Review finding 8: a held phone that was unplugged kept the daemon alive forever."""
    fake = FakeAdapter([dev("A")])
    o = orch(fake, tmp_path)
    o.connect("android:A")
    fake.devices.clear()
    assert o.held_count() == 0


def test_resolve_accepts_bare_serial(tmp_path: Path) -> None:
    assert orch(FakeAdapter([dev("A")]), tmp_path).resolve("A").id == "android:A"


def test_screenshot_on_unauthorized_device_raises_not_ready(tmp_path: Path) -> None:
    with pytest.raises(DeviceNotReady) as e:
        orch(FakeAdapter([dev("A", DeviceState.UNAUTHORIZED)]), tmp_path).screenshot("android:A")
    assert "Allow USB debugging" in (e.value.hint or "")


def test_screenshot_after_device_vanishes(tmp_path: Path) -> None:
    fake = FakeAdapter([dev("A")])
    o = orch(fake, tmp_path)
    o.devices()
    fake.devices.clear()
    with pytest.raises(DeviceNotFound):
        o.screenshot("android:A")


def test_unsupported_capability_is_refused_with_reason(tmp_path: Path) -> None:
    fake = FakeAdapter([dev("A")], unsupported={Capability.SCREENSHOT: "x"})
    with pytest.raises(CapabilityUnsupported) as e:
        orch(fake, tmp_path).screenshot("android:A")
    assert "x" in str(e.value) and fake.calls == []


def test_screenshot_saves_artifact_with_png_size(tmp_path: Path) -> None:
    art = orch(FakeAdapter([dev("A")]), tmp_path).screenshot("android:A")
    assert (art.width, art.height) == (4, 8)
    assert Path(art.path).exists() and art.device_id == "android:A"


def test_connect_holds_and_disconnect_releases(tmp_path: Path) -> None:
    o = orch(FakeAdapter([dev("A")]), tmp_path)
    assert o.connect("android:A").held and o.held_count() == 1
    o.disconnect("android:A")
    assert o.held_count() == 0 and not o.devices()[0].held


def test_disconnect_wifi_device_drops_network_link(tmp_path: Path) -> None:
    fake = FakeAdapter()
    o = orch(fake, tmp_path)
    d = o.connect_wifi("10.0.0.5", 5555)
    assert d.transport is Transport.WIFI and d.id == "android:10.0.0.5:5555"
    o.disconnect(d.id)
    assert ("disconnect_wifi", "10.0.0.5:5555") in fake.calls


def test_live_view_returns_viewer_pid(tmp_path: Path) -> None:
    assert orch(FakeAdapter([dev("A")]), tmp_path).live_view("android:A") == 4242
