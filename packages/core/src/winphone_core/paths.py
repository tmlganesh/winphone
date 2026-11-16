import os
from pathlib import Path


def app_dir() -> Path:
    """Per-user state folder: %LOCALAPPDATA%\\winphone, or WINPHONE_HOME when set (tests)."""
    override = os.environ.get("WINPHONE_HOME")
    base = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "winphone"
    base.mkdir(parents=True, exist_ok=True)
    return base
