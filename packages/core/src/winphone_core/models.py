from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class DeviceState(StrEnum):
    ONLINE = "online"
    UNAUTHORIZED = "unauthorized"
    OFFLINE = "offline"


NOT_READY_HINTS = {
    "unauthorized": "Unlock the phone and accept 'Allow USB debugging'",
    "offline": "Replug the cable, or toggle USB debugging off and on",
}


class Transport(StrEnum):
    USB = "usb"
    WIFI = "wifi"


class DeviceInfo(BaseModel):
    id: str
    platform: Platform
    serial: str
    state: DeviceState
    transport: Transport
    model: str | None = None
    os_version: str | None = None
    sdk: int | None = None
    held: bool = False


class Capability(StrEnum):
    OBSERVE_TREE = "observe_tree"
    SCREENSHOT = "screenshot"
    TAP = "tap"
    SWIPE = "swipe"
    TYPE = "type"
    KEYS = "keys"
    LAUNCH_APP = "launch_app"
    LIST_APPS = "list_apps"
    INSTALL_APP = "install_app"
    LOGS = "logs"
    RECORD = "record"
    LIVE_VIEW = "live_view"
    WIRELESS = "wireless"


class CapabilityStatus(BaseModel):
    capability: Capability
    supported: bool
    reason: str | None = None


PACKAGE_PATTERN = r"^[A-Za-z]\w*(\.[A-Za-z_]\w*)+$"  # an Android package name; never starts with '-'


class Key(StrEnum):
    BACK = "back"
    HOME = "home"
    RECENTS = "recents"
    ENTER = "enter"


@dataclass(frozen=True)
class UiSnapshot:
    """A raw UI-hierarchy dump plus what normalising it needs."""

    xml: str
    foreground_app: str | None
    width: int
    height: int


class ScreenshotArtifact(BaseModel):
    device_id: str
    path: str
    width: int
    height: int
    taken_at: datetime
