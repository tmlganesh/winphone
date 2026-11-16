"""Which phone platforms the daemon can serve: every adapter whose tool is installed."""

from __future__ import annotations

from winphone_android.adapter import AndroidAdapter
from winphone_core.adapter import DeviceAdapter
from winphone_core.errors import ToolMissing
from winphone_ios.adapter import WindowsIOSAdapter


def load_adapters() -> tuple[list[DeviceAdapter], list[str]]:
    """A missing tool (say adb, for an iPhone-only user) skips that platform instead of stopping the daemon;
    `winphone doctor` reports what is missing."""
    loaded: list[DeviceAdapter] = []
    skipped: list[str] = []
    for factory in (AndroidAdapter, WindowsIOSAdapter):
        try:
            loaded.append(factory())
        except ToolMissing as e:
            skipped.append(f"{factory.__name__}: {e.message}")
    return loaded, skipped
