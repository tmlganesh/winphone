from typing import Protocol

from winphone_core.models import CapabilityStatus, DeviceInfo, Platform


class DeviceAdapter(Protocol):
    """The only way the orchestrator reaches a phone. Adapters are synchronous; the daemon
    runs them in its thread pool."""

    platform: Platform

    def discover(self) -> list[DeviceInfo]: ...

    def capabilities(self, serial: str) -> list[CapabilityStatus]: ...

    def screenshot(self, serial: str) -> bytes:
        """PNG bytes of the current screen."""
        ...

    def live_view(self, serial: str) -> int:
        """Open a live viewer for the device; returns the viewer's process id."""
        ...

    def pair(self, host: str, port: int, code: str) -> None: ...

    def connect_wifi(self, host: str, port: int) -> str:
        """Connect over the network; returns the device serial."""
        ...

    def disconnect_wifi(self, serial: str) -> None: ...
