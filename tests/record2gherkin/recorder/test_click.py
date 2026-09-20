"""spec 7 用例 2 / 3 / 4 / 12 / 13：click 事件与 target 语义字段。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import events_of_type, read_events


def test_click_basic_fields(recorder_page: Page) -> None:
    """用例 2：点提交按钮，click 事件的 target 语义字段完整（name 取 aria-label）。"""
    recorder_page.click("#submit-btn")

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    event = clicks[0]
    target = event["target"]
    assert target["tag"] == "button"
    assert target["role"] == "button"
    assert target["name"] == "提交订单"
    assert target["testid"] == "submit-order"
    assert target["id"] == "submit-btn"
    assert target["ordinal"] is None
    assert target["form_label"] is None
    assert event["value"] is None
    assert "demo_form.html" in event["url"]
    assert event["page_title"] == "Demo 下单页"


def test_click_name_priority_aria_label_over_text(recorder_page: Page) -> None:
    """用例 3：aria-label 与 innerText 不同时，name 取 aria-label。"""
    recorder_page.click("#refresh-btn")

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    assert recorder_page.eval_on_selector("#refresh-btn", "el => el.innerText") == "刷新"
    assert clicks[0]["target"]["name"] == "刷新列表"


def test_click_name_falls_to_label_wrapper(recorder_page: Page) -> None:
    """用例 4：点 label 内的 span，name 取 associated label 文本而非 span 自身 innerText。"""
    recorder_page.click("#newsletter-icon")

    clicks = events_of_type(read_events(recorder_page), "click")
    icon_clicks = [click for click in clicks if click["target"]["testid"] == "newsletter-icon"]
    assert len(icon_clicks) == 1
    target = icon_clicks[0]["target"]
    assert recorder_page.eval_on_selector("#newsletter-icon", "el => el.innerText") == "★"
    assert target["tag"] == "span"
    assert target["name"] == "★ 订阅邮件"
    assert target["name"] != "★"
    assert target["form_label"] is None


def test_ordinal_duplicate_names(recorder_page: Page) -> None:
    """用例 12：两个同名「删除」按钮，点第二个时 ordinal=2。"""
    recorder_page.locator(".delete-btn").nth(1).click()

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    target = clicks[0]["target"]
    assert target["name"] == "删除"
    assert target["testid"] == "delete-second"
    assert target["ordinal"] == 2


def test_ordinal_null_when_unique(recorder_page: Page) -> None:
    """用例 13：名称唯一的按钮 ordinal=null。"""
    recorder_page.click("#show-result")

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    target = clicks[0]["target"]
    assert target["name"] == "显示结果"
    assert target["ordinal"] is None
