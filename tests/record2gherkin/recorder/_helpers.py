"""record2gherkin recorder 测试公共工具：路径、事件读取、契约校验。

spec 8.3 的事实校验入口：`validate_event_stream` 复用同一份 schema 校验逻辑。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

RECORDER_DIR = Path(__file__).resolve().parents[3] / "record2gherkin" / "recorder"
RECORDER_SOURCE_PATH = RECORDER_DIR / "recorder.js"
BUILD_SCRIPT_PATH = RECORDER_DIR / "build_bookmarklet.py"
BOOKMARKLET_PATH = RECORDER_DIR / "bookmarklet.txt"
REPO_ROOT = RECORDER_DIR.parents[1]
DEMO_FORM_PATH = Path(__file__).resolve().parent / "fixtures" / "demo_form.html"

SNAPSHOT_DELAY_MS = 300
EVENT_KEYS = {"seq", "ts", "type", "url", "page_title", "target", "value", "dom_snapshot"}
TARGET_KEYS = {"tag", "role", "name", "testid", "id", "ordinal", "form_label"}
EVENT_TYPES = {"navigate", "click", "input", "select", "submit"}
VALUED_EVENT_TYPES = {"input", "select"}
MAX_ASSERT_TEXTS = 8
MAX_ASSERT_TEXT_LENGTH = 120
MAX_TARGET_NAME_LENGTH = 200
MAX_INPUT_VALUE_LENGTH = 500
MAX_SELECT_VALUE_LENGTH = 200


def load_recorder_source() -> str:
    return RECORDER_SOURCE_PATH.read_text(encoding="utf-8")


def read_recording(page: Page) -> dict[str, Any]:
    return json.loads(page.evaluate("() => R2GRecorder.getJSON()"))


def read_events(page: Page) -> list[dict[str, Any]]:
    return read_recording(page)["events"]


def events_of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == event_type]


def wait_for_snapshot(page: Page) -> None:
    """等待 300ms 延迟快照落地。"""
    page.wait_for_timeout(SNAPSHOT_DELAY_MS + 200)


def _validate_target(target: Any) -> None:
    assert isinstance(target, dict), "target 必须是对象或 null"
    assert set(target) == TARGET_KEYS, f"target 字段集不符: {sorted(target)}"
    assert isinstance(target["tag"], str) and target["tag"], "target.tag 必须是非空字符串"
    assert isinstance(target["role"], str) and target["role"], "target.role 必须是非空字符串"
    assert isinstance(target["name"], str), "target.name 必须是字符串"
    assert len(target["name"]) <= MAX_TARGET_NAME_LENGTH, "target.name 超过 200 字符"
    assert target["testid"] is None or isinstance(target["testid"], str)
    assert target["id"] is None or isinstance(target["id"], str)
    ordinal = target["ordinal"]
    assert ordinal is None or (isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 1)
    assert target["form_label"] is None or isinstance(target["form_label"], str)
    if target["name"] == "":
        assert ordinal is None, "name 为空时 ordinal 必须为 null"


def validate_event_stream(payload: Any) -> None:
    """校验事件流符合 recorder spec 1/2/3 契约，不符合时抛 AssertionError。"""
    assert isinstance(payload, dict), "顶层必须是对象"
    assert set(payload) == {"session", "events"}, "顶层键必须是 session/events"
    session = payload["session"]
    assert isinstance(session, dict) and set(session) == {"started_at", "origin"}, "session 结构不符"
    assert isinstance(session["started_at"], str) and session["started_at"].endswith("Z"), "started_at 必须是 UTC ISO 8601"
    datetime.fromisoformat(session["started_at"])
    assert isinstance(session["origin"], str), "session.origin 必须是字符串"

    events = payload["events"]
    assert isinstance(events, list) and events, "events 必须是非空数组"
    for index, event in enumerate(events, start=1):
        assert isinstance(event, dict), "事件必须是对象"
        assert EVENT_KEYS <= set(event), f"事件缺字段: {sorted(EVENT_KEYS - set(event))}"
        assert event["seq"] == index, f"seq 必须从 1 起严格递增，期望 {index} 实得 {event['seq']}"
        assert isinstance(event["ts"], int) and not isinstance(event["ts"], bool), "ts 必须是整数毫秒"
        assert event["type"] in EVENT_TYPES, f"未知事件类型: {event['type']}"
        assert isinstance(event["url"], str) and event["url"], "url 必须是非空字符串"
        assert isinstance(event["page_title"], str), "page_title 必须是字符串"

        target = event["target"]
        if event["type"] == "navigate":
            assert target is None, "navigate 的 target 必须为 null"
        if target is not None:
            _validate_target(target)

        value = event["value"]
        if event["type"] in VALUED_EVENT_TYPES:
            assert isinstance(value, str), f"{event['type']} 必须带字符串 value"
            assert len(value) <= MAX_INPUT_VALUE_LENGTH, "value 超过 500 字符"
        else:
            assert value is None, f"{event['type']} 的 value 必须为 null"
        if event["type"] == "select":
            assert len(value) <= MAX_SELECT_VALUE_LENGTH, "select value 超过 200 字符"

        snapshot = event["dom_snapshot"]
        assert isinstance(snapshot, dict) and set(snapshot) == {"assert_texts"}, "dom_snapshot 结构不符"
        assert_texts = snapshot["assert_texts"]
        assert isinstance(assert_texts, list), "assert_texts 必须是数组"
        assert len(assert_texts) <= MAX_ASSERT_TEXTS, "assert_texts 超过 8 条"
        for text in assert_texts:
            assert isinstance(text, str) and len(text) <= MAX_ASSERT_TEXT_LENGTH, "assert_texts 条目超长"
