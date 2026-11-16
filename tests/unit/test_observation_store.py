from __future__ import annotations

from datetime import UTC, datetime

from winphone_core.errors import (
    StaleReference,
    TargetAmbiguous,
    TargetNotFound,
    error_from_dict,
)
from winphone_core.observation import Element, Observation, ObservationStore, reresolve


def el(ref: str, text: str | None = None, rid: str | None = None, y: int = 0) -> Element:
    return Element(ref=ref, role="button", text=text, resource_id=rid, cls="android.widget.Button",
                   bounds=(0, y, 100, y + 50), clickable=True)


def ob(oid: str, elements: list[Element], device: str = "android:A") -> Observation:
    return Observation(device_id=device, observation_id=oid, revision=oid, timestamp=datetime.now(UTC),
                       width=1080, height=2400, rotation=0, foreground_app="com.example", elements=elements)


def test_store_keeps_last_n_per_device() -> None:
    s = ObservationStore(keep=2)
    for i in range(3):
        s.put(ob(f"o{i}", []))
    s.put(ob("x", [], device="android:B"))
    assert s.get("android:A", "o0") is None
    assert s.get("android:A", "o1") is not None
    latest = s.latest("android:A")
    assert latest is not None and latest.observation_id == "o2"
    assert s.latest("android:Z") is None


def test_reresolve_unique_match() -> None:
    new = ob("o2", [el("e1", "Cancel", y=10), el("e2", "OK", "com.x:id/ok", y=900)])
    found = reresolve(el("e5", "OK", "com.x:id/ok", y=100), new)
    assert found is not None and found.ref == "e2"


def test_reresolve_two_matches_is_none() -> None:
    new = ob("o2", [el("e1", "OK", y=10), el("e2", "OK", y=900)])
    assert reresolve(el("e5", "OK"), new) is None


def test_reresolve_without_id_or_text_is_none() -> None:
    new = ob("o2", [el("e1", y=10)])
    assert reresolve(el("e5"), new) is None


def test_new_error_codes_roundtrip() -> None:
    for cls, exit_code in ((StaleReference, 1), (TargetNotFound, 1), (TargetAmbiguous, 2)):
        e = error_from_dict(cls("x").to_dict())
        assert type(e) is cls and e.exit_code == exit_code
