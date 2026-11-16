from typing import Protocol

from winphone_core.models import CapabilityStatus, DeviceInfo, Key, Platform, UiSnapshot


class DeviceAdapter(Protocol):
    """The only way the orchestrator reaches a phone. Adapters are synchronous; the daemon
    runs them in its thread pool.

    Input methods raise AdapterError when nothing reached the phone, and OutcomeUnknown when the
    input may have been delivered (the engine then observes instead of retrying)."""

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

    def ui_snapshot(self, serial: str) -> UiSnapshot: ...

    def tap(self, serial: str, x: int, y: int) -> None: ...

    def swipe(self, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...

    def type_text(self, serial: str, text: str) -> None:
        """Set the text of the focused field. The text must never appear in an error or log."""
        ...

    def press(self, serial: str, key: Key) -> None: ...

    def launch_app(self, serial: str, package: str) -> None: ...

    def list_apps(self, serial: str, include_system: bool) -> list[str]: ...

    def release(self, serial: str) -> None:
        """Stop any helper the adapter started on the phone; a no-op if there is none."""
        ...
