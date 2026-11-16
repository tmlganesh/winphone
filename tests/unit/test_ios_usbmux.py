"""The usbmuxd client against a real local TCP server that speaks the usbmux plist protocol."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fakes import IPHONE_UDID, closed_port, start_fake_usbmux
from winphone_ios.usbmux import MuxDevice, MuxUnavailable, has_pair_record, list_devices, lockdown_values


@pytest.fixture
def mux() -> Iterator[tuple[str, int]]:
    server, address = start_fake_usbmux()
    yield address
    server.shutdown()
    server.server_close()


def test_list_devices_parses_mux_reply(mux: tuple[str, int]) -> None:
    assert list_devices(address=mux) == [MuxDevice(device_id=9, udid=IPHONE_UDID, connection="USB"),
                                         MuxDevice(device_id=12, udid=IPHONE_UDID, connection="Network")]


def test_no_usbmuxd_raises_fast() -> None:
    t = time.perf_counter()
    with pytest.raises(MuxUnavailable):
        list_devices(address=closed_port())
    assert time.perf_counter() - t < 1.0


def test_has_pair_record_true_false(mux: tuple[str, int]) -> None:
    assert has_pair_record(IPHONE_UDID, address=mux)
    assert not has_pair_record("0" * 40, address=mux)


def test_lockdown_values(mux: tuple[str, int]) -> None:
    got = lockdown_values(9, ["ProductType", "ProductVersion", "DeviceName"], address=mux)
    assert got == {"ProductType": "iPhone10,6", "ProductVersion": "16.7.16"}
