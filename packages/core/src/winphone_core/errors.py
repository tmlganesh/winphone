"""Typed errors shared by the daemon, its clients and the adapters.

Each error carries a stable `code` (sent over the wire) and the CLI exit code it maps to.
"""

from __future__ import annotations

from typing import Any, ClassVar


class WinphoneError(Exception):
    code: ClassVar[str] = "error"
    exit_code: ClassVar[int] = 1

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "hint": self.hint}


class DeviceNotFound(WinphoneError):
    code = "device_not_found"
    exit_code = 3


class DeviceNotReady(WinphoneError):
    code = "device_not_ready"
    exit_code = 3


class DeviceAmbiguous(WinphoneError):
    code = "device_ambiguous"
    exit_code = 2


class CapabilityUnsupported(WinphoneError):
    code = "capability_unsupported"
    exit_code = 4


class PermissionDenied(WinphoneError):
    code = "permission_denied"
    exit_code = 5


class OutcomeUnknown(WinphoneError):
    code = "outcome_unknown"
    exit_code = 6


class DaemonUnavailable(WinphoneError):
    code = "daemon_unavailable"
    exit_code = 7


class ToolMissing(WinphoneError):
    code = "tool_missing"


class ChecksumMismatch(WinphoneError):
    code = "checksum_mismatch"


class AdapterError(WinphoneError):
    code = "adapter_error"


class InvalidRequest(WinphoneError):
    code = "invalid_request"
    exit_code = 2


class StaleReference(WinphoneError):
    code = "stale_reference"


class TargetNotFound(WinphoneError):
    code = "target_not_found"


class TargetAmbiguous(WinphoneError):
    code = "target_ambiguous"
    exit_code = 2


_BY_CODE: dict[str, type[WinphoneError]] = {
    cls.code: cls
    for cls in (
        InvalidRequest,
        StaleReference,
        TargetNotFound,
        TargetAmbiguous,
        DeviceNotFound,
        DeviceNotReady,
        DeviceAmbiguous,
        CapabilityUnsupported,
        PermissionDenied,
        OutcomeUnknown,
        DaemonUnavailable,
        ToolMissing,
        ChecksumMismatch,
        AdapterError,
    )
}


def error_from_dict(d: dict[str, Any]) -> WinphoneError:
    cls = _BY_CODE.get(str(d.get("error")), WinphoneError)
    return cls(str(d.get("message", "")), d.get("hint"))
