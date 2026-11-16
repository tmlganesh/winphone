"""Observation normaliser. Fixtures are hand-written, never a dump of a real person's screen."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from winphone_core.observation import Observation, Selector, find, normalize, to_compact

DEFAULTS = {
    "text": "", "resource-id": "", "class": "android.widget.FrameLayout", "package": "com.example",
    "content-desc": "", "checkable": "false", "checked": "false", "clickable": "false", "enabled": "true",
    "focusable": "false", "focused": "false", "scrollable": "false", "password": "false", "selected": "false",
    "bounds": "[0,0][1080,2400]",
}


def node(children: str = "", **attrs: str) -> str:
    merged = {**DEFAULTS, **{k.replace("_", "-"): v for k, v in attrs.items()}}
    a = " ".join(f'{k}="{v}"' for k, v in merged.items())
    return f"<node {a}>{children}</node>"


def screen(clock: str = "12:30", welcome: str = "Welcome", rotation: str = "0") -> str:
    status = node(node(text=clock, **{"class": "android.widget.TextView"}, package="com.android.systemui",
                       bounds="[20,10][120,70]"),
                  package="com.android.systemui", bounds="[0,0][1080,80]")
    body = node(
        node(text=welcome, **{"class": "android.widget.TextView"}, bounds="[100,300][980,400]")
        + node(resource_id="com.example:id/email", **{"class": "android.widget.EditText"}, clickable="true",
               focusable="true", bounds="[100,500][980,620]")
        + node(text="hunter2", password="true", resource_id="com.example:id/password",
               **{"class": "android.widget.EditText"}, clickable="true", focusable="true",
               bounds="[100,700][980,820]")
        + node(text="Sign in", resource_id="com.example:id/login", **{"class": "android.widget.Button"},
               clickable="true", bounds="[340,1580][740,1660]")
        + node(content_desc="Help", **{"class": "android.widget.ImageButton"}, clickable="true",
               bounds="[900,2200][1000,2300]")
        + node(text="Hidden", **{"class": "android.widget.TextView"}, bounds="[0,0][0,0]")
        + node(text="Offscreen", **{"class": "android.widget.TextView"}, bounds="[0,3000][100,3100]"),
        **{"class": "android.widget.LinearLayout"}, bounds="[0,80][1080,2400]")
    return (f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\r\n"
            f'<hierarchy rotation="{rotation}">{status}{node(body, bounds="[0,80][1080,2400]")}</hierarchy>')


def obs(xml: str | None = None, app: str = "com.example") -> Observation:
    return normalize(xml or screen(), device_id="android:A", observation_id="o1", foreground_app=app,
                     width=1080, height=2400)


def test_layout_containers_dropped() -> None:
    assert all(e.cls != "android.widget.LinearLayout" for e in obs().elements)
    assert all(e.cls != "android.widget.FrameLayout" for e in obs().elements)


def test_zero_area_and_offscreen_dropped() -> None:
    texts = {e.text for e in obs().elements}
    assert "Hidden" not in texts and "Offscreen" not in texts


def test_refs_in_document_order() -> None:
    o = obs()
    assert [e.ref for e in o.elements] == [f"e{i}" for i in range(1, len(o.elements) + 1)]
    assert [e.text or e.desc or e.resource_id for e in o.elements] == [
        "12:30", "Welcome", "com.example:id/email", "com.example:id/password", "Sign in", "Help"]


def test_secure_field_has_no_text() -> None:
    pw = next(e for e in obs().elements if e.resource_id == "com.example:id/password")
    assert pw.secure and pw.text is None
    assert "hunter2" not in obs().model_dump_json() and "hunter2" not in to_compact(obs())


def test_roles() -> None:
    roles = {(e.text or e.desc or e.resource_id): e.role for e in obs().elements}
    assert roles["Sign in"] == "button" and roles["Help"] == "button"
    assert roles["com.example:id/email"] == "textbox" and roles["Welcome"] == "text"


def test_revision_ignores_system_ui() -> None:
    assert obs(screen(clock="12:30")).revision == obs(screen(clock="12:31")).revision
    assert obs(screen(welcome="Welcome")).revision != obs(screen(welcome="Goodbye")).revision
    assert obs(app="com.example").revision != obs(app="com.other").revision


def test_rotation_read_from_root() -> None:
    assert obs(screen(rotation="1")).rotation == 1


def test_find_by_resource_id_suffix() -> None:
    assert [e.text for e in find(obs(), Selector(resource_id="login"))] == ["Sign in"]
    assert [e.text for e in find(obs(), Selector(resource_id="com.example:id/login"))] == ["Sign in"]


def test_find_text_contains_case_insensitive() -> None:
    assert [e.text for e in find(obs(), Selector(text_contains="SIGN"))] == ["Sign in"]
    assert find(obs(), Selector(text="sign in")) == []


def test_find_fields_combine() -> None:
    assert [e.desc for e in find(obs(), Selector(role="button", clickable=True, desc="Help"))] == ["Help"]


def test_compact_line_format() -> None:
    lines = to_compact(obs()).splitlines()
    assert lines[0].startswith("# app=com.example 1080x2400 rot=0 rev=")
    assert lines[0].endswith("obs=o1 elements=6")
    assert 'e5 button "Sign in" id=login @540,1620 click' in lines
    assert 'e6 button desc="Help" @950,2250 click' in lines
    assert "e4 textbox id=password @540,760 click edit secure" in lines


def test_selector_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        Selector()
