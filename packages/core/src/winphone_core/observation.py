"""Screen observations: a UI-hierarchy dump normalised into a short list of elements with references.

Screen text is untrusted data. It is stored and returned, never interpreted.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from pydantic import BaseModel, model_validator

_ROLES = {
    "Button": "button", "ImageButton": "button", "EditText": "textbox", "AutoCompleteTextView": "textbox",
    "MultiAutoCompleteTextView": "textbox", "CheckBox": "checkbox", "Switch": "switch",
    "SwitchCompat": "switch", "SwitchMaterial": "switch", "RadioButton": "radio", "ImageView": "image",
    "TextView": "text", "RecyclerView": "list", "ListView": "list", "ScrollView": "list",
    "HorizontalScrollView": "list", "SeekBar": "slider", "ProgressBar": "progress", "WebView": "web",
}
# Excluded from the revision so a ticking status-bar clock never reads as "the screen changed".
_SYSTEM_UI = "com.android.systemui"
_MAX_TEXT = 80


class Element(BaseModel):
    ref: str
    role: str
    text: str | None = None
    desc: str | None = None
    resource_id: str | None = None
    cls: str
    package: str | None = None
    bounds: tuple[int, int, int, int]
    clickable: bool = False
    scrollable: bool = False
    focusable: bool = False
    checkable: bool = False
    checked: bool = False
    enabled: bool = True
    selected: bool = False
    secure: bool = False

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


class Observation(BaseModel):
    device_id: str
    observation_id: str
    revision: str
    timestamp: datetime
    width: int
    height: int
    rotation: int
    foreground_app: str | None
    elements: list[Element]
    screenshot_path: str | None = None


class Selector(BaseModel):
    text: str | None = None
    text_contains: str | None = None
    desc: str | None = None
    resource_id: str | None = None
    role: str | None = None
    clickable: bool | None = None

    @model_validator(mode="after")
    def _one_field(self) -> Selector:
        if all(v is None for v in self.model_dump().values()):
            raise ValueError("a selector needs at least one field")
        return self


def _bounds(raw: str) -> tuple[int, int, int, int]:
    nums = [int(n) for n in re.findall(r"-?\d+", raw)]
    return (nums[0], nums[1], nums[2], nums[3]) if len(nums) == 4 else (0, 0, 0, 0)


def _visible(b: tuple[int, int, int, int], width: int, height: int) -> bool:
    x1, y1, x2, y2 = b
    return x2 > x1 and y2 > y1 and x2 > 0 and y2 > 0 and x1 < width and y1 < height


def _revision(elements: list[Element], app: str | None, width: int, height: int, rotation: int) -> str:
    parts = [[e.role, e.text, e.desc, e.resource_id, e.bounds, e.clickable, e.checked, e.enabled, e.selected]
             for e in elements if e.package != _SYSTEM_UI]
    payload = json.dumps([app, width, height, rotation, parts], separators=(",", ":"), default=list)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def normalize(xml: str, *, device_id: str, observation_id: str, foreground_app: str | None,
              width: int, height: int) -> Observation:
    root = ET.fromstring(xml.strip())
    rotation = int(root.get("rotation") or 0)
    elements: list[Element] = []
    for n in root.iter("node"):
        def flag(name: str, node: ET.Element = n) -> bool:
            return node.get(name) == "true"

        cls = n.get("class") or ""
        role = _ROLES.get(cls.rsplit(".", 1)[-1], "view")
        secure = flag("password")
        text = None if secure else (n.get("text") or None)
        desc = n.get("content-desc") or None
        rid = n.get("resource-id") or None
        bounds = _bounds(n.get("bounds") or "")
        interesting = (text or desc or rid or flag("clickable") or flag("scrollable") or flag("checkable")
                       or role == "textbox" or secure)
        if not interesting or not _visible(bounds, width, height):
            continue
        elements.append(Element(
            ref=f"e{len(elements) + 1}", role=role, text=text, desc=desc, resource_id=rid, cls=cls,
            package=n.get("package") or None, bounds=bounds, clickable=flag("clickable"),
            scrollable=flag("scrollable"), focusable=flag("focusable"), checkable=flag("checkable"),
            checked=flag("checked"), enabled=n.get("enabled") != "false", selected=flag("selected"),
            secure=secure,
        ))
    return Observation(
        device_id=device_id, observation_id=observation_id,
        revision=_revision(elements, foreground_app, width, height, rotation),
        timestamp=datetime.now(UTC), width=width, height=height, rotation=rotation,
        foreground_app=foreground_app, elements=elements,
    )


class ObservationStore:
    """The last few observations per device; element refs are only valid against these."""

    def __init__(self, keep: int = 8) -> None:
        self._keep = keep
        self._by_device: dict[str, list[Observation]] = {}
        self._latest: dict[str, Observation] = {}
        self._lock = threading.Lock()

    def put(self, obs: Observation, latest: bool = True) -> None:
        """`latest=False` keeps an observation for explicit lookup without making it what a bare ref
        means (an action's after-screen, which the caller has not seen)."""
        with self._lock:
            kept = self._by_device.setdefault(obs.device_id, [])
            kept.append(obs)
            del kept[: -self._keep]
            if latest:
                self._latest[obs.device_id] = obs

    def latest(self, device_id: str) -> Observation | None:
        with self._lock:
            return self._latest.get(device_id)

    def get(self, device_id: str, observation_id: str) -> Observation | None:
        with self._lock:
            return next((o for o in self._by_device.get(device_id, []) if o.observation_id == observation_id),
                        None)


def reresolve(old: Element, new: Observation) -> Element | None:
    """The same element on a changed screen: exactly one match on (resource id, text, class), else None."""
    if old.resource_id is None and old.text is None:
        return None
    matches = [e for e in new.elements
               if (e.resource_id, e.text, e.cls) == (old.resource_id, old.text, old.cls)]
    return matches[0] if len(matches) == 1 else None


def _short_id(rid: str) -> str:
    return rid.split(":id/", 1)[1] if ":id/" in rid else rid


def find(obs: Observation, sel: Selector) -> list[Element]:
    def ok(e: Element) -> bool:
        return all((
            sel.text is None or e.text == sel.text,
            sel.text_contains is None or sel.text_contains.lower() in (e.text or "").lower(),
            sel.desc is None or e.desc == sel.desc,
            sel.resource_id is None
            or (e.resource_id is not None and sel.resource_id in (e.resource_id, _short_id(e.resource_id))),
            sel.role is None or e.role == sel.role,
            sel.clickable is None or e.clickable == sel.clickable,
        ))

    return [e for e in obs.elements if ok(e)]


def _quote(s: str) -> str:
    return json.dumps(s if len(s) <= _MAX_TEXT else s[: _MAX_TEXT - 3] + "...", ensure_ascii=False)


def to_compact(obs: Observation) -> str:
    """One header line plus one line per element: what an agent reads instead of a screenshot."""
    lines = [f"# app={obs.foreground_app or '?'} {obs.width}x{obs.height} rot={obs.rotation} "
             f"rev={obs.revision} obs={obs.observation_id} elements={len(obs.elements)}"]
    for e in obs.elements:
        parts = [e.ref, e.role]
        if e.text:
            parts.append(_quote(e.text))
        if e.desc and e.desc != e.text:
            parts.append(f"desc={_quote(e.desc)}")
        if e.resource_id:
            parts.append(f"id={_short_id(e.resource_id)}")
        parts.append("@{},{}".format(*e.center))
        flags = [name for name, on in (("click", e.clickable), ("scroll", e.scrollable),
                                       ("edit", e.role == "textbox")) if on]
        if e.checkable:
            flags.append("checked" if e.checked else "unchecked")
        if not e.enabled:
            flags.append("disabled")
        if e.secure:
            flags.append("secure")
        lines.append(" ".join(parts + flags))
    return "\n".join(lines)
