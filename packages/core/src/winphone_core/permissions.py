"""Who may do what (design §10). Enforced in the daemon, which every client passes through.

- OBSERVE (read the screen, logs, apps): anyone.
- INTERACT (tap, swipe, type, keys, launch): humans; agents only while a human-approved grant is live.
- SENSITIVE (install, sensitive apps, Send/Pay/Delete-like targets): a human click per action.

Agents can only *ask*: every grant and every sensitive action goes through `confirm`, a window on the
laptop that a human answers (no answer = no). Known limit: a program running as the same Windows user can
claim to be a human client; WINPHONE closes the paths through its own tools and documents the rest.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from winphone_core.audit import AuditLog
from winphone_core.errors import PermissionDenied

CLIENT_HEADER = "X-Winphone-Client"
MAX_GRANT_MINUTES = 240
_LABEL_CHARS = 40


class Tier(StrEnum):
    OBSERVE = "observe"
    INTERACT = "interact"
    SENSITIVE = "sensitive"


class ClientKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


def client_kind(header: str | None) -> ClientKind:
    """A missing or unknown header is an agent: the safe default."""
    return ClientKind.HUMAN if header == ClientKind.HUMAN.value else ClientKind.AGENT


DEFAULT_SENSITIVE_PACKAGES = frozenset({
    "com.android.settings", "com.android.vending",  # system settings, Play Store
    "com.google.android.apps.messaging", "com.android.mms", "com.samsung.android.messaging",
    "com.whatsapp", "com.whatsapp.w4b", "org.telegram.messenger", "com.google.android.gm",
    "com.google.android.apps.walletnfcrel", "com.google.android.apps.nbu.paisa.user", "com.phonepe.app",
    "net.one97.paytm", "in.org.npci.upiapp", "in.amazon.mShop.android.shopping",
    # The phone's own installer and permission screens: otherwise 'installs always ask' could be sidestepped.
    "com.android.packageinstaller", "com.google.android.packageinstaller",
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
})
DENY_COOLDOWN_S = 10.0  # after a Deny, an agent's next asks within this time are refused without a window
DEFAULT_SENSITIVE_LABELS = ("send", "pay", "buy", "purchase", "delete", "transfer", "confirm", "uninstall",
                            "reset", "erase", "place order", "sign out", "log out")
_INPUT_KINDS = {"tap", "swipe", "type", "press"}


class SensitivityPolicy:
    def __init__(self, packages: Iterable[str], labels: Iterable[str]) -> None:
        self.packages = frozenset(packages)
        self._label = re.compile(r"\b(" + "|".join(re.escape(w) for w in labels) + r")\b", re.IGNORECASE)

    @classmethod
    def default(cls) -> SensitivityPolicy:
        return cls(DEFAULT_SENSITIVE_PACKAGES, DEFAULT_SENSITIVE_LABELS)

    def tier_of(self, kind: str, foreground_app: str | None, target_label: str | None) -> Tier:
        if kind in ("install", "uninstall"):
            return Tier.SENSITIVE
        if kind in _INPUT_KINDS:
            if foreground_app in self.packages or (target_label and self._label.search(target_label)):
                return Tier.SENSITIVE
            return Tier.INTERACT
        if kind == "launch":
            return Tier.INTERACT  # opening an app is harmless; acting inside a sensitive one is not
        return Tier.OBSERVE


def quoted(label: str | None) -> str:
    """Screen text is untrusted: shown short and quoted, never as instructions."""
    if not label:
        return ""
    # Control, line-break and invisible/bidi characters could rearrange what the window appears to say.
    clean = "".join(c if c.isprintable() else " " for c in label).replace('"', "'")
    short = clean if len(clean) <= _LABEL_CHARS else clean[: _LABEL_CHARS - 1] + "…"
    return f'"{short}"'


@dataclass(frozen=True)
class Grant:
    device_id: str
    expires_at: float


class PermissionManager:
    def __init__(self, confirm: Callable[[str], bool], audit: AuditLog, policy: SensitivityPolicy,
                 clock: Callable[[], float] = time.time) -> None:
        self._confirm = confirm
        self.audit = audit
        self.policy = policy
        self._clock = clock
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()
        self._window = threading.Lock()  # one approval window at a time; never a queue of them
        self._quiet_until = 0.0

    def _ask(self, client: ClientKind, message: str) -> bool:
        if client is ClientKind.AGENT and self._clock() < self._quiet_until:
            raise PermissionDenied("the human just said no", hint="Wait, and ask the human before trying again")
        if not self._window.acquire(blocking=False):
            raise PermissionDenied("another approval is already waiting on the laptop",
                                   hint="Wait for the human to answer it first")
        try:
            ok = bool(self._confirm(message))
        except Exception:  # noqa: BLE001 - a window that fails to show is a 'no'
            ok = False
        finally:
            self._window.release()
        if not ok:
            self._quiet_until = self._clock() + DENY_COOLDOWN_S
        return ok

    def grants(self) -> list[Grant]:
        now = self._clock()
        with self._lock:
            for device_id in [d for d, g in self._grants.items() if g.expires_at <= now]:
                del self._grants[device_id]
            return list(self._grants.values())

    def _granted(self, device_id: str) -> bool:
        return any(g.device_id == device_id for g in self.grants())

    def request_grant(self, client: ClientKind, device_id: str, minutes: int) -> Grant:
        """Agents and humans alike: a grant exists only after a human clicks Allow."""
        minutes = max(1, min(minutes, MAX_GRANT_MINUTES))
        who = "An AI agent" if client is ClientKind.AGENT else "Your WINPHONE console"
        try:
            ok = self._ask(client, f"{who} asks to tap, swipe and type on {device_id} for {minutes} minutes.\n\n"
                                   "Sensitive actions (installs, messaging, payments, Settings) will still ask "
                                   "each time.\n\nAllow?")
        except PermissionDenied as e:
            self.audit.write(event="grant", client=client.value, device=device_id, minutes=minutes,
                             decision="deny", reason=e.message)
            raise
        self.audit.write(event="grant", client=client.value, device=device_id, minutes=minutes,
                         decision="allow" if ok else "deny")
        if not ok:
            raise PermissionDenied(f"the human did not allow interacting with {device_id}",
                                   hint="Ask the human to try again and click Allow")
        grant = Grant(device_id, self._clock() + minutes * 60)
        with self._lock:
            self._grants[device_id] = grant
        return grant

    def revoke(self, device_id: str | None) -> int:
        with self._lock:
            gone = [d for d in self._grants if device_id is None or d == device_id]
            for d in gone:
                del self._grants[d]
        self.audit.write(event="revoke", device=device_id or "*", revoked=len(gone))
        return len(gone)

    def require(self, client: ClientKind, device_id: str, tier: Tier, what: str, **details: object) -> bool:
        """Raises PermissionDenied, or returns True when a human approved it in a window just now (the screen
        may have moved while they read it, so the caller must look again before acting)."""
        if tier is Tier.OBSERVE:
            return False
        if tier is Tier.INTERACT:
            ok = client is ClientKind.HUMAN or self._granted(device_id)
            self.audit.write(event="decision", client=client.value, device=device_id, tier=tier.value,
                             what=what, decision="allow" if ok else "deny", **details)
            if not ok:
                raise PermissionDenied(f"{what}: an AI agent needs the human's permission to interact with "
                                       f"{device_id}", hint="Call phone_authorize (the human clicks Allow on "
                                                            "the laptop) or ask the human to run winphone "
                                                            "authorize grant")
            return False
        who = "An AI agent" if client is ClientKind.AGENT else "Your WINPHONE console"
        try:
            ok = self._ask(client, f"{who} wants to: {what}\nPhone: {device_id}\n\n"
                                   "This is a sensitive action. Allow it once?")
        except PermissionDenied as e:
            self.audit.write(event="decision", client=client.value, device=device_id, tier=tier.value, what=what,
                             decision="deny", reason=e.message, **details)
            raise
        self.audit.write(event="decision", client=client.value, device=device_id, tier=tier.value, what=what,
                         decision="allow" if ok else "deny", **details)
        if not ok:
            raise PermissionDenied(f"{what}: the human did not allow this sensitive action",
                                   hint="Nothing was done. Ask the human before trying again")
        return True
