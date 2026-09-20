"""spec 7 用例 17 / 18：全事件 schema 契约校验与 stop 冻结事件流。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import (
    read_recording,
    validate_event_stream,
    wait_for_snapshot,
)


def test_schema_contract_all_events(recorder_page: Page) -> None:
    """用例 17：走完整「填表 -> 提交」流程，逐事件校验 Schema v1 契约。"""
    email = recorder_page.locator("#email")
    email.fill("user@example.com")
    email.blur()

    password = recorder_page.locator("#password")
    password.fill("topsecret")
    password.blur()

    recorder_page.select_option("#shipping", "express")
    recorder_page.locator("#newsletter").check()
    recorder_page.click("#submit-btn")

    wait_for_snapshot(recorder_page)
    payload = read_recording(recorder_page)
    validate_event_stream(payload)

    assert {event["type"] for event in payload["events"]} == {"navigate", "input", "select", "click", "submit"}


def test_stop_freezes_stream(recorder_page: Page) -> None:
    """用例 18：stop 后 click/fill/pushState 都不再产生事件，getJSON 仍可调用。"""
    recorder_page.click("#show-result")
    wait_for_snapshot(recorder_page)
    before = read_recording(recorder_page)

    assert recorder_page.evaluate("() => R2GRecorder.stop()") is True
    assert recorder_page.evaluate("() => R2GRecorder.stop()") is False
    assert recorder_page.evaluate("() => R2GRecorder.status().recording") is False

    recorder_page.click("#refresh-btn")
    email = recorder_page.locator("#email")
    email.fill("after-stop")
    email.blur()
    recorder_page.evaluate("() => history.pushState({}, '', '#after-stop')")
    wait_for_snapshot(recorder_page)

    after = read_recording(recorder_page)
    assert after["events"] == before["events"]
    assert recorder_page.evaluate("() => R2GRecorder.status().event_count") == len(after["events"])
    # spec 5：copy() 恒返回 Promise<boolean>（剪贴板权限由环境决定，不断言具体结果）
    assert recorder_page.evaluate("() => R2GRecorder.copy().then((value) => typeof value)") == "boolean"
