"""Append-only audit trail of permission decisions and action results (JSON lines).

Never written to it: typed text (only its length), tokens, screen text beyond a short quoted label."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> None:
        line = json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), **fields}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
