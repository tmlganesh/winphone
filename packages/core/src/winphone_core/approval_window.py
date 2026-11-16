"""The human's Allow/Deny window. Shown by the daemon on the laptop's screen. No answer within the timeout
counts as Deny. Known limit: like any window, a program running as the same Windows user could send it
keystrokes; WINPHONE's own tools never do."""

from __future__ import annotations

import ctypes

TIMEOUT_MS = 60_000
_MB_YESNO, _MB_ICONWARNING, _MB_DEFBUTTON2, _MB_TOPMOST = 0x4, 0x30, 0x100, 0x40000
# On top of every window but NOT given keyboard focus (no MB_SETFOREGROUND): otherwise a 'y' typed into
# another window while it pops up would answer Yes. Default button is No.
WINDOW_FLAGS = _MB_YESNO | _MB_ICONWARNING | _MB_DEFBUTTON2 | _MB_TOPMOST
_IDYES = 6


def confirm_window(message: str, timeout_ms: int = TIMEOUT_MS) -> bool:
    """True only if the human clicked Yes. 'No', closing the window or the timeout (32000) are all False."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    box = user32.MessageBoxTimeoutW  # exported by user32 since Windows XP; adds the timeout MessageBoxW lacks
    box.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_ushort,
                    ctypes.c_uint]
    box.restype = ctypes.c_int
    seconds = timeout_ms // 1000
    text = f"{message}\n\n(No answer in {seconds} seconds counts as No.)"
    return int(box(None, text, "WINPHONE - permission needed", WINDOW_FLAGS, 0, timeout_ms)) == _IDYES
