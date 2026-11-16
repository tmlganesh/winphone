from __future__ import annotations

import re
import struct
from datetime import UTC, datetime
from pathlib import Path

from winphone_core.errors import AdapterError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk, which the PNG spec requires to come first."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise AdapterError("the device did not return a PNG image", hint=data[:80].decode(errors="replace"))
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


class ArtifactStore:
    def __init__(self, root: Path, keep: int = 50) -> None:
        self.root = root
        self.keep = keep

    def save_screenshot(self, device_id: str, png: bytes) -> Path:
        folder = self.root / _safe(device_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = folder / f"{stamp}.png"
        n = 1
        while path.exists():
            path = folder / f"{stamp}-{n}.png"
            n += 1
        path.write_bytes(png)
        for old in sorted(folder.glob("*.png"))[: -self.keep]:
            old.unlink(missing_ok=True)
        return path
