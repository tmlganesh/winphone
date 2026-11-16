"""The action lifecycle: resolve target -> execute -> wait -> observe -> verify -> receipt."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import HOME, LOGIN, LOGIN_SHIFTED, FakeAdapter, dev, ui_xml
from winphone_core import actions
from winphone_core.actions import Action, Target, Wait
from winphone_core.artifacts import ArtifactStore
from winphone_core.errors import (
    CapabilityUnsupported,
    OutcomeUnknown,
    StaleReference,
    TargetAmbiguous,
    TargetNotFound,
)
from winphone_core.models import Capability, Key
from winphone_core.observation import Selector
from winphone_core.orchestrator import Orchestrator

A = "android:A"


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions, "POLL_S", 0.01)
    monkeypatch.setattr(actions, "FOCUS_SETTLE_S", 0.0)


def setup(tmp_path: Path, **kw: object) -> tuple[Orchestrator, FakeAdapter]:
    fake = FakeAdapter([dev("A")], **kw)  # type: ignore[arg-type]
    return Orchestrator([fake], ArtifactStore(tmp_path)), fake


def tap_ref(ref: str) -> Action:
    return Action(kind="tap", target=Target(ref=ref))


def test_observe_returns_elements_and_stores_them(tmp_path: Path) -> None:
    o, _ = setup(tmp_path)
    obs = o.observe(A)
    assert [e.text for e in obs.elements] == [None, "Sign in"] and obs.observation_id


def test_tap_by_ref_verified_when_screen_changes(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    o.observe(A)
    r = o.act(A, tap_ref("e2"))
    assert r.outcome == "verified" and r.resolved_by == "ref" and r.before_revision != r.after_revision
    assert ("tap", "540", "1620") in fake.calls


def test_tap_not_verified_when_screen_unchanged(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    fake.advance_on_input = False
    o.observe(A)
    r = o.act(A, tap_ref("e2"), wait=Wait(revision_changed=True, timeout_s=0.05))
    assert r.outcome == "not_verified" and "unchanged" in r.evidence


def test_tap_on_changed_screen_reresolves_unique(tmp_path: Path) -> None:
    o, fake = setup(tmp_path, screens=[(LOGIN, "com.example"), (LOGIN_SHIFTED, "com.example"), (HOME, "com.example")])
    o.observe(A)
    fake.index = 1  # the screen changed after the agent looked
    r = o.act(A, tap_ref("e2"))
    assert r.resolved_by == "reresolved" and r.target_ref == "e3"
    assert ("tap", "540", "1820") in fake.calls


def test_tap_on_changed_screen_without_match_is_stale_and_does_not_tap(tmp_path: Path) -> None:
    o, fake = setup(tmp_path, screens=[(LOGIN, "com.example"), (HOME, "com.example")])
    o.observe(A)
    fake.index = 1
    with pytest.raises(StaleReference) as e:
        o.act(A, tap_ref("e2"))
    assert not any(c[0] == "tap" for c in fake.calls)
    assert e.value.hint and "o2" in e.value.hint  # points at the new observation


def test_selector_matching_two_is_ambiguous(tmp_path: Path) -> None:
    ok = {"text": "OK", "class": "android.widget.Button", "clickable": "true"}
    o, fake = setup(tmp_path, screens=[(ui_xml({**ok, "bounds": "[0,0][100,100]"},
                                              {**ok, "bounds": "[0,200][100,300]"}), "com.example")])
    with pytest.raises(TargetAmbiguous) as e:
        o.act(A, Action(kind="tap", target=Target(selector=Selector(text="OK"))))
    assert "e1" in str(e.value) and "e2" in str(e.value) and fake.calls == []


def test_selector_not_found(tmp_path: Path) -> None:
    o, _ = setup(tmp_path)
    with pytest.raises(TargetNotFound):
        o.act(A, Action(kind="tap", target=Target(selector=Selector(text="Nope"))))


def test_wait_text_visible_verified_and_timeout(tmp_path: Path) -> None:
    o, _ = setup(tmp_path)
    r = o.act(A, Action(kind="tap", target=Target(selector=Selector(text="Sign in"))),
              wait=Wait(text_visible="Welcome", timeout_s=1))
    assert r.outcome == "verified" and r.resolved_by == "selector"
    o2, _ = setup(tmp_path)
    r2 = o2.act(A, Action(kind="tap", target=Target(selector=Selector(text="Sign in"))),
                wait=Wait(text_visible="Nope", timeout_s=0.05))
    assert r2.outcome == "not_verified" and "Nope" in r2.evidence


def test_outcome_unknown_is_never_retried(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    fake.input_error = OutcomeUnknown("tap may or may not have happened (HTTPTimeoutError)")
    o.observe(A)
    r = o.act(A, tap_ref("e2"))
    assert r.outcome == "unknown" and [c for c in fake.calls if c[0] == "tap"] == [("tap", "540", "1620")]
    assert r.observation_id  # what the screen showed afterwards


def test_idempotency_key_returns_same_receipt(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    o.observe(A)
    first = o.act(A, tap_ref("e2"), idempotency_key="k1")
    again = o.act(A, tap_ref("e2"), idempotency_key="k1")
    assert again.action_id == first.action_id and len([c for c in fake.calls if c[0] == "tap"]) == 1


def test_typed_text_never_in_receipt(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    o.observe(A)
    secret = "hunter" + "2"
    action = Action(kind="type", target=Target(ref="e1"), text=secret)
    r = o.act(A, action)
    assert secret not in r.model_dump_json() and secret not in repr(action)
    assert "6 characters" not in r.evidence and "7 characters" in r.evidence
    assert ("type", "7") in fake.calls


def test_capability_checked_before_acting(tmp_path: Path) -> None:
    o, fake = setup(tmp_path, unsupported={Capability.TAP: "blocked by vendor"})
    with pytest.raises(CapabilityUnsupported):
        o.act(A, Action(kind="tap", target=Target(point=(10, 10))))
    assert fake.calls == []


def test_launch_waits_for_app_foreground(tmp_path: Path) -> None:
    o, fake = setup(tmp_path, screens=[(HOME, "com.launcher"), (HOME, "com.example")])
    r = o.act(A, Action(kind="launch", package="com.example"))
    assert r.outcome == "verified" and "com.example" in r.evidence and ("launch", "com.example") in fake.calls


def test_swipe_direction_uses_screen_quarters(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    o.act(A, Action(kind="swipe", direction="up"))
    assert ("swipe", "540", "1800", "540", "600") in fake.calls


def test_press_key(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    assert o.act(A, Action(kind="press", key=Key.BACK)).outcome == "verified"
    assert ("press", "back") in fake.calls


def test_action_validation() -> None:
    with pytest.raises(ValueError):
        Action(kind="tap")  # no target
    with pytest.raises(ValueError):
        Action(kind="type", text=None)
    with pytest.raises(ValueError):
        Target(ref="e1", point=(1, 2))


STATUS_ICON = {"content-desc": "Notification", "package": "com.android.systemui", "class": "android.widget.ImageView",
               "bounds": "[900,10][960,70]"}
CANCEL = {"text": "Cancel", "class": "android.widget.Button", "clickable": "true", "bounds": "[100,1500][500,1600]"}
DELETE = {"text": "Delete all", "class": "android.widget.Button", "clickable": "true", "bounds": "[340,1660][740,1740]"}
TITLE = {"text": "Inbox", "bounds": "[100,200][980,300]"}


def test_status_bar_change_never_retargets_a_ref(tmp_path: Path) -> None:
    """Review C1: a status-bar icon vanished, refs shifted, the revision did not change."""
    before = ui_xml(STATUS_ICON, TITLE, CANCEL, DELETE)  # Cancel is e3
    after = ui_xml(TITLE, CANCEL, DELETE)  # same app screen: Cancel is now e2, Delete all is e3
    o, fake = setup(tmp_path, screens=[(before, "com.example"), (after, "com.example")])
    o.observe(A)
    fake.index = 1
    r = o.act(A, tap_ref("e3"))
    assert ("tap", "300", "1550") in fake.calls  # Cancel's centre, never Delete all's
    assert r.resolved_by == "reresolved" and r.target_ref == "e2"


def test_ref_past_the_end_after_status_change_is_not_a_crash(tmp_path: Path) -> None:
    """Same revision, but e4 no longer exists (the icon before it went away): re-resolve, never StopIteration."""
    before = ui_xml(STATUS_ICON, TITLE, CANCEL, DELETE)  # Delete all is e4
    after = ui_xml(TITLE, CANCEL, DELETE)  # Delete all is e3; there is no e4
    o, fake = setup(tmp_path, screens=[(before, "com.example"), (after, "com.example")])
    o.observe(A)
    fake.index = 1
    r = o.act(A, tap_ref("e4"))
    assert r.resolved_by == "reresolved" and ("tap", "540", "1700") in fake.calls


def test_ref_resolves_against_the_observation_the_caller_saw(tmp_path: Path) -> None:
    """Review C2: after one tap, the engine's own observations must not become the caller's 'latest'."""
    o, fake = setup(tmp_path)
    o.observe(A)  # LOGIN: e1 password, e2 Sign in
    assert o.act(A, tap_ref("e2")).outcome == "verified"  # now on HOME: e1 is "Welcome back"
    with pytest.raises(StaleReference):
        o.act(A, tap_ref("e1"))  # the caller means the password field, which is gone
    assert [c for c in fake.calls if c[0] == "tap"] == [("tap", "540", "1620")]


def test_receipt_observation_can_be_targeted_explicitly(tmp_path: Path) -> None:
    o, fake = setup(tmp_path, screens=[(LOGIN, "com.example"), (HOME, "com.example")])
    o.observe(A)
    r = o.act(A, tap_ref("e2"))
    fake.advance_on_input = False
    again = o.act(A, Action(kind="tap", target=Target(ref="e1", observation_id=r.observation_id)),
                  wait=Wait(revision_changed=True, timeout_s=0.05))
    assert again.resolved_by == "ref" and ("tap", "540", "350") in fake.calls


def test_phone_lost_after_input_is_unknown_and_idempotent(tmp_path: Path) -> None:
    """Review I3: a failure after the input was sent must be an 'unknown' receipt, never an error."""
    o, fake = setup(tmp_path)
    o.observe(A)
    fake.break_snapshots_after_input = True
    r = o.act(A, tap_ref("e2"), idempotency_key="k")
    assert r.outcome == "unknown"
    again = o.act(A, tap_ref("e2"), idempotency_key="k")
    assert again.action_id == r.action_id and len([c for c in fake.calls if c[0] == "tap"]) == 1


def test_type_into_failing_after_its_tap_is_unknown(tmp_path: Path) -> None:
    """Review I4: the focusing tap was delivered, so 'failed' (nothing happened) would be false."""
    from winphone_core.errors import AdapterError

    o, fake = setup(tmp_path)
    o.observe(A)
    fake.type_error = AdapterError("typing not sent: no focused text field")
    assert o.act(A, Action(kind="type", text="x", target=Target(ref="e1"))).outcome == "unknown"
    assert o.act(A, Action(kind="type", text="x")).outcome == "failed"


def test_disconnect_and_close_release_the_helper(tmp_path: Path) -> None:
    o, fake = setup(tmp_path)
    o.observe(A)
    o.close()
    assert ("release", "A") in fake.calls
