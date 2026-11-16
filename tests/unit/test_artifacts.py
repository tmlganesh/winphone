from pathlib import Path

import pytest
from fakes import make_png
from winphone_core.artifacts import ArtifactStore, png_size
from winphone_core.errors import AdapterError

PNG_1x2 = make_png(1, 2)


def test_wifi_serial_dir_is_windows_safe(tmp_path: Path) -> None:
    p = ArtifactStore(tmp_path).save_screenshot("android:192.168.1.5:5555", PNG_1x2)
    assert ":" not in str(p.relative_to(tmp_path))
    assert p.read_bytes() == PNG_1x2


def test_prunes_beyond_keep(tmp_path: Path) -> None:
    s = ArtifactStore(tmp_path, keep=2)
    for _ in range(3):
        s.save_screenshot("android:x", PNG_1x2)
    assert len(list((tmp_path / "android_x").iterdir())) == 2


def test_png_size_reads_ihdr() -> None:
    assert png_size(make_png(1080, 2400)) == (1080, 2400)


def test_png_size_rejects_non_png() -> None:
    with pytest.raises(AdapterError):
        png_size(b"error: device offline")
