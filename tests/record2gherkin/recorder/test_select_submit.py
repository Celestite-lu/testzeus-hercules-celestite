"""spec 7 用例 9 / 10：select 事件与 submit 事件。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import (
    events_of_type,
    read_events,
    wait_for_snapshot,
)


def test_select_event(recorder_page: Page) -> None:
    """用例 9：select 只产生 1 条 select（无 input），value 为选中 option 可见文本。"""
    recorder_page.select_option("#shipping", "express")

    wait_for_snapshot(recorder_page)
    events = read_events(recorder_page)
    selects = events_of_type(events, "select")
    assert len(selects) == 1

    event = selects[0]
    assert event["value"] == "次日达"
    assert event["target"]["tag"] == "select"
    assert event["target"]["role"] == "combobox"
    assert event["target"]["name"] == "配送方式"
    assert event["target"]["form_label"] == "配送方式"
    assert events_of_type(events, "input") == []


def test_submit_event(recorder_page: Page) -> None:
    """用例 10：点 submit 按钮 -> click 与 submit 各 1 条，submit 的 target 指向 form。"""
    recorder_page.click("#submit-btn")

    wait_for_snapshot(recorder_page)
    events = read_events(recorder_page)
    clicks = events_of_type(events, "click")
    submits = events_of_type(events, "submit")

    assert len(clicks) == 1
    assert len(submits) == 1
    assert clicks[0]["seq"] < submits[0]["seq"]

    target = submits[0]["target"]
    assert target["tag"] == "form"
    assert target["id"] == "order-form"
    assert target["role"] == "generic"
    assert target["testid"] is None
    assert target["form_label"] is None
    assert isinstance(target["name"], str) and target["name"]
    assert len(target["name"]) <= 200

    assert submits[0]["value"] is None
    assert "demo_form.html" in submits[0]["url"]
    # fixture 表单 onsubmit="return false"，录制器不得触发导航
    assert recorder_page.url.endswith("demo_form.html")
