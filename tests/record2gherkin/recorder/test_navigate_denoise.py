"""spec 7 用例 1 / 11 / 16 / 19：初始 navigate、去噪、SPA 路由 navigate、重复 start。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import (
    events_of_type,
    read_events,
    read_recording,
    wait_for_snapshot,
)


def test_start_emits_initial_navigate(recorder_page: Page) -> None:
    """用例 1：start 后立即取 JSON，events[0] 是当前页的 navigate，无 assert 基线。"""
    payload = read_recording(recorder_page)
    events = payload["events"]

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "navigate"
    assert event["seq"] == 1
    assert "demo_form.html" in event["url"]
    assert event["target"] is None
    assert event["value"] is None
    assert event["dom_snapshot"] == {"assert_texts": []}
    assert event["page_title"] == "Demo 下单页"
    assert payload["session"]["origin"] == recorder_page.evaluate("() => location.origin")

    # 无 assert 基线：等待也不会把整页已有文本写成断言素材
    wait_for_snapshot(recorder_page)
    assert read_events(recorder_page) == events


def test_hover_scroll_ignored(recorder_page: Page) -> None:
    """用例 11：hover / mousemove / wheel 滚动不产生任何事件。"""
    before = read_events(recorder_page)

    recorder_page.hover("#hover-target")
    recorder_page.mouse.wheel(0, 300)
    recorder_page.mouse.move(10, 10)
    wait_for_snapshot(recorder_page)

    assert read_events(recorder_page) == before


def test_navigate_history_pushstate(recorder_page: Page) -> None:
    """用例 16：伪 SPA 链接 pushState 后新增 1 条 navigate，url 为跳转后 URL。"""
    recorder_page.click("#spa-link")
    wait_for_snapshot(recorder_page)

    navigates = events_of_type(read_events(recorder_page), "navigate")
    assert len(navigates) == 2
    assert navigates[0]["seq"] < navigates[1]["seq"]
    assert navigates[1]["url"].endswith("#view2")
    assert navigates[1]["target"] is None
    assert navigates[1]["value"] is None
    assert recorder_page.url.endswith("#view2")


def test_double_start_noop(recorder_page: Page) -> None:
    """用例 19：重复 start 返回 false，初始 navigate 仍只有 1 条。"""
    assert recorder_page.evaluate("() => R2GRecorder.start()") is False

    events = read_events(recorder_page)
    assert len(events) == 1
    assert len(events_of_type(events, "navigate")) == 1
