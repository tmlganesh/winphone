from pathlib import Path

import pytest
from winphone_core.errors import DeviceAmbiguous, WinphoneError, error_from_dict
from winphone_core.paths import app_dir


def test_error_roundtrip_keeps_class_and_exit_code() -> None:
    e = error_from_dict(DeviceAmbiguous("2 phones", hint="pass --device").to_dict())
    assert type(e) is DeviceAmbiguous
    assert e.exit_code == 2
    assert e.hint == "pass --device"
    assert str(e) == "2 phones"


def test_unknown_error_code_falls_back_to_base() -> None:
    e = error_from_dict({"error": "nope", "message": "x", "hint": None})
    assert type(e) is WinphoneError
    assert e.exit_code == 1


def test_app_dir_honours_winphone_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINPHONE_HOME", str(tmp_path / "h"))
    assert app_dir() == tmp_path / "h"
    assert app_dir().is_dir()
