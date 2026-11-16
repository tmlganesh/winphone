"""A minimal client for Apple's usbmuxd (Apple Mobile Device Service, 127.0.0.1:27015 on Windows).

Only what discovery needs, so listing phones stays in milliseconds: list devices, check for a pairing
record, and read a few public lockdown values without pairing. Everything else goes through go-ios.
Protocol: 16-byte little-endian header (length, version 1, type 8 = plist, tag) + XML plist; after a
successful Connect the socket carries lockdown's 4-byte big-endian length + plist messages.
"""

from __future__ import annotations

import plistlib
import socket
import struct
from dataclasses import dataclass
from typing import Any

USBMUXD = ("127.0.0.1", 27015)
LOCKDOWN_PORT = 62078
_PROG = {"ProgName": "winphone", "ClientVersionString": "winphone"}


@dataclass(frozen=True)
class MuxDevice:
    device_id: int
    udid: str
    connection: str  # "USB" or "Network"


def _read_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("usbmuxd closed the connection")
        data += chunk
    return data


def _mux(s: socket.socket, payload: dict[str, Any]) -> dict[str, Any]:
    body = plistlib.dumps({**payload, **_PROG})
    s.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, 1) + body)
    length, = struct.unpack("<I", _read_exact(s, 4))
    reply: dict[str, Any] = plistlib.loads(_read_exact(s, length - 4)[12:])
    return reply


CONNECT_TIMEOUT_S = 0.3  # a local service accepts at once; Windows retries a refused localhost port for ~2 s


def _connect(address: tuple[str, int], timeout: float) -> socket.socket:
    s = socket.create_connection(address, timeout=CONNECT_TIMEOUT_S)
    s.settimeout(timeout)
    return s


class MuxUnavailable(OSError):
    """Apple's usbmuxd is not reachable (Apple Devices / iTunes not installed, or its service stopped)."""


def list_devices(timeout: float = 2.0, address: tuple[str, int] = USBMUXD) -> list[MuxDevice]:
    """Attached iPhones. Raises MuxUnavailable when Apple's service is not there."""
    try:
        with _connect(address, timeout) as s:
            reply = _mux(s, {"MessageType": "ListDevices"})
    except (OSError, ValueError, struct.error) as e:
        raise MuxUnavailable(f"Apple Mobile Device Service not reachable at {address[0]}:{address[1]}") from e
    out = []
    for d in reply.get("DeviceList", []):
        p = d.get("Properties", {})
        if "SerialNumber" in p:
            out.append(MuxDevice(int(p["DeviceID"]), str(p["SerialNumber"]), str(p.get("ConnectionType", "USB"))))
    return out


def has_pair_record(udid: str, timeout: float = 2.0, address: tuple[str, int] = USBMUXD) -> bool:
    """True when this PC holds a pairing record for the iPhone (the user tapped Trust)."""
    try:
        with _connect(address, timeout) as s:
            return "PairRecordData" in _mux(s, {"MessageType": "ReadPairRecord", "PairRecordID": udid})
    except (OSError, ValueError, struct.error):
        return False


def lockdown_values(device_id: int, keys: list[str], timeout: float = 3.0,
                    address: tuple[str, int] = USBMUXD) -> dict[str, str]:
    """Public values lockdownd answers without pairing (ProductType, ProductVersion ...)."""
    out: dict[str, str] = {}
    try:
        with _connect(address, timeout) as s:
            port = socket.htons(LOCKDOWN_PORT)
            if _mux(s, {"MessageType": "Connect", "DeviceID": device_id, "PortNumber": port}).get("Number") != 0:
                return out
            for key in keys:
                body = plistlib.dumps({"Label": "winphone", "Request": "GetValue", "Key": key})
                s.sendall(struct.pack(">I", len(body)) + body)
                n, = struct.unpack(">I", _read_exact(s, 4))
                reply = plistlib.loads(_read_exact(s, n))
                if "Value" in reply:
                    out[key] = str(reply["Value"])
    except (OSError, ValueError, struct.error):
        pass  # partial values are still useful; discovery must not fail on one slow phone
    return out
