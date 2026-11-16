"""The action lifecycle: fresh observation -> resolve target -> execute -> wait -> observe -> receipt.

Never retries: an input that may have been delivered ends as outcome "unknown" with what the screen
shows now. Typed text never appears in a receipt (only its length)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from winphone_core.adapter import DeviceAdapter
from winphone_core.errors import AdapterError, OutcomeUnknown, StaleReference, TargetAmbiguous, TargetNotFound
from winphone_core.models import PACKAGE_PATTERN, Capability, Key
from winphone_core.observation import Element, Observation, Selector, find, reresolve

POLL_S = 0.3
FOCUS_SETTLE_S = 0.3  # after tapping a field, before typing into it
SWIPE_MS = 300
DEFAULT_WAIT_S = 3.0

Kind = Literal["tap", "swipe", "type", "press", "launch"]
CAPABILITY: dict[str, Capability] = {"tap": Capability.TAP, "swipe": Capability.SWIPE, "type": Capability.TYPE,
                                     "press": Capability.KEYS, "launch": Capability.LAUNCH_APP}


class Target(BaseModel):
    ref: str | None = None
    observation_id: str | None = None  # with ref; default: the device's latest observation
    selector: Selector | None = None
    point: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Target:
        if sum(x is not None for x in (self.ref, self.selector, self.point)) != 1:
            raise ValueError("a target is exactly one of ref, selector or point")
        if self.observation_id is not None and self.ref is None:
            raise ValueError("observation_id only goes with ref")
        return self


class Action(BaseModel):
    kind: Kind
    target: Target | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    to: tuple[int, int] | None = None
    text: str | None = Field(default=None, repr=False)
    key: Key | None = None
    package: str | None = Field(default=None, pattern=PACKAGE_PATTERN)

    @model_validator(mode="after")
    def _fits_kind(self) -> Action:
        problems = {
            "tap": self.target is None and "tap needs a target",
            "swipe": ((self.direction is None) == (self.to is None) and "swipe needs a direction or a 'to' point")
            or (self.to is not None and self.target is None and "swipe with 'to' needs a start target"),
            "type": self.text is None and "type needs text",
            "press": self.key is None and "press needs a key",
            "launch": self.package is None and "launch needs a package",
        }[self.kind]
        if problems:
            raise ValueError(problems)
        return self


class Wait(BaseModel):
    text_visible: str | None = None
    text_gone: str | None = None
    element: Selector | None = None
    app_foreground: str | None = None
    revision_changed: bool = False
    timeout_s: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def _one_condition(self) -> Wait:
        set_ = [self.text_visible, self.text_gone, self.element, self.app_foreground]
        if sum(x is not None for x in set_) + int(self.revision_changed) != 1:
            raise ValueError("wait for exactly one condition")
        return self


class Receipt(BaseModel):
    action_id: str
    device_id: str
    kind: Kind
    target_ref: str | None
    resolved_by: Literal["ref", "reresolved", "selector", "point"] | None
    before_revision: str
    after_revision: str
    outcome: Literal["verified", "not_verified", "unknown", "failed"]
    evidence: str
    duration_ms: int
    observation_id: str


Observe = Callable[[], Observation]
Previous = Callable[[str | None], Observation | None]
Resolved = tuple[Element | None, tuple[int, int] | None, Literal["ref", "reresolved", "selector", "point"] | None]


def _identity(e: Element) -> tuple[object, ...]:
    return e.package, e.cls, e.text, e.desc, e.resource_id, e.bounds


def _resolve(target: Target | None, fresh: Observation, base: Observation | None) -> Resolved:
    """`base` is the observation the ref came from, looked up before `fresh` was taken."""
    if target is None:
        return None, None, None
    if target.point is not None:
        return None, target.point, "point"
    if target.selector is not None:
        matches = find(fresh, target.selector)
        if not matches:
            raise TargetNotFound(f"nothing on screen matches {target.selector.model_dump(exclude_none=True)}",
                                 hint=f"Observation {fresh.observation_id} is the current screen")
        if len(matches) > 1:
            raise TargetAmbiguous(f"{len(matches)} elements match: {', '.join(e.ref for e in matches)}",
                                  hint="Tap one by ref, or narrow the selector")
        return matches[0], matches[0].center, "selector"
    if base is None:
        raise TargetNotFound(f"no observation {target.observation_id or ''} to resolve {target.ref} against".strip(),
                             hint="Observe the screen first")
    old = next((e for e in base.elements if e.ref == target.ref), None)
    if old is None:
        raise TargetNotFound(f"{target.ref} is not in observation {base.observation_id}")
    # Compare the element itself, not the revision: status-bar changes shift refs without changing it.
    same = next((e for e in fresh.elements if e.ref == target.ref), None)
    if same is not None and _identity(same) == _identity(old):
        return same, same.center, "ref"
    moved = reresolve(old, fresh)
    if moved is None:
        raise StaleReference(f"the screen changed since {base.observation_id} and {target.ref} cannot be found again",
                             hint=f"Use the current screen, observation {fresh.observation_id}")
    return moved, moved.center, "reresolved"


def _quarters(box: tuple[int, int, int, int], direction: str) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    cx, cy, w, h = (x1 + x2) // 2, (y1 + y2) // 2, x2 - x1, y2 - y1
    near_y, far_y, near_x, far_x = y1 + int(h * 0.25), y1 + int(h * 0.75), x1 + int(w * 0.25), x1 + int(w * 0.75)
    return {"up": (cx, far_y, cx, near_y), "down": (cx, near_y, cx, far_y),
            "left": (far_x, cy, near_x, cy), "right": (near_x, cy, far_x, cy)}[direction]


def _execute(action: Action, adapter: DeviceAdapter, serial: str, fresh: Observation,
             el: Element | None, point: tuple[int, int] | None) -> None:
    if action.kind == "tap" and point is not None:
        adapter.tap(serial, *point)
    elif action.kind == "swipe":
        if action.to is not None and point is not None:
            adapter.swipe(serial, *point, *action.to, SWIPE_MS)
        elif action.direction is not None:
            box = el.bounds if el is not None else (0, 0, fresh.width, fresh.height)
            adapter.swipe(serial, *_quarters(box, action.direction), SWIPE_MS)
    elif action.kind == "type" and action.text is not None:
        if point is None:
            adapter.type_text(serial, action.text)
            return
        adapter.tap(serial, *point)
        time.sleep(FOCUS_SETTLE_S)
        try:
            adapter.type_text(serial, action.text)
        except AdapterError as e:  # the focusing tap was delivered: something did happen
            raise OutcomeUnknown(f"tapped the field, then {e.message}") from None
    elif action.kind == "press" and action.key is not None:
        adapter.press(serial, action.key)
    elif action.kind == "launch" and action.package is not None:
        adapter.launch_app(serial, action.package)


def _visible(obs: Observation, text: str) -> bool:
    return any(text in (e.text or "") or text in (e.desc or "") for e in obs.elements)


def _check(w: Wait, before: Observation, after: Observation) -> tuple[bool, str]:
    if w.text_visible is not None:
        return _visible(after, w.text_visible), f"text {w.text_visible!r} visible"
    if w.text_gone is not None:
        return not _visible(after, w.text_gone), f"text {w.text_gone!r} gone"
    if w.element is not None:
        return bool(find(after, w.element)), f"element {w.element.model_dump(exclude_none=True)} present"
    if w.app_foreground is not None:
        return after.foreground_app == w.app_foreground, f"{w.app_foreground} in foreground"
    return after.revision != before.revision, "screen changed"


def _wait(w: Wait, before: Observation, observe: Observe) -> tuple[bool, str, Observation]:
    deadline = time.monotonic() + w.timeout_s
    while True:
        after = observe()
        ok, what = _check(w, before, after)
        if ok:
            return True, what, after
        if time.monotonic() >= deadline:
            if w.revision_changed:
                return False, f"screen unchanged after {w.timeout_s:g} s", after
            return False, f"not met after {w.timeout_s:g} s: {what} (foreground {after.foreground_app})", after
        time.sleep(POLL_S)


def default_wait(action: Action) -> Wait:
    if action.kind == "launch" and action.package is not None:
        return Wait(app_foreground=action.package, timeout_s=10)
    return Wait(revision_changed=True, timeout_s=DEFAULT_WAIT_S)


def run(action: Action, wait: Wait | None, *, adapter: DeviceAdapter, serial: str, observe: Observe,
        previous: Previous, record: Callable[[Observation], None]) -> Receipt:
    """`observe` must not publish its observations as the caller's latest; `record` keeps the one a
    receipt points at, so it can be targeted by observation_id."""
    started = time.monotonic()
    # Look up what the caller saw before observing again: the fresh observation becomes "latest".
    target = action.target
    base = previous(target.observation_id) if target is not None and target.ref is not None else None
    fresh = observe()
    el, point, how = _resolve(target, fresh, base)

    def receipt(outcome: Literal["verified", "not_verified", "unknown", "failed"], evidence: str,
                after: Observation) -> Receipt:
        if action.kind == "type" and action.text is not None:
            evidence = f"typed {len(action.text)} characters; {evidence}"
        record(after)
        return Receipt(action_id=uuid.uuid4().hex[:12], device_id=fresh.device_id, kind=action.kind,
                       target_ref=el.ref if el is not None else None, resolved_by=how,
                       before_revision=fresh.revision, after_revision=after.revision, outcome=outcome,
                       evidence=evidence, duration_ms=int((time.monotonic() - started) * 1000),
                       observation_id=after.observation_id)

    try:
        _execute(action, adapter, serial, fresh, el, point)
    except OutcomeUnknown as e:
        try:
            after = observe()
        except Exception as lost:  # noqa: BLE001 - the input may be on the phone: report, never raise
            return receipt("unknown", f"{e.message}; then could not read the screen ({type(lost).__name__})",
                           fresh)
        return receipt("unknown", f"{e.message}; foreground now {after.foreground_app}", after)
    except AdapterError as e:
        return receipt("failed", e.message, fresh)
    try:
        ok, evidence, after = _wait(wait or default_wait(action), fresh, observe)
    except Exception as lost:  # noqa: BLE001 - the input was sent: an error now would invite a retry
        return receipt("unknown", f"input sent; could not read the screen afterwards ({type(lost).__name__})",
                       fresh)
    return receipt("verified" if ok else "not_verified", evidence, after)
