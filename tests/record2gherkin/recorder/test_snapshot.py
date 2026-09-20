"""spec 7 用例 14 / 15：dom_snapshot.assert_texts 差集采集与截断上限。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import (
    events_of_type,
    read_events,
    wait_for_snapshot,
)


def test_assert_texts_diff_after_click(recorder_page: Page) -> None:
    """用例 14：点「显示结果」后，新出现的可见文本进入该 click 的 assert_texts 差集。"""
    recorder_page.click("#show-result")
    wait_for_snapshot(recorder_page)

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    assert_texts = clicks[0]["dom_snapshot"]["assert_texts"]

    assert "订单已提交成功" in assert_texts
    # 差集语义：加载即存在的文本（基线）不构成断言依据
    assert "Demo 下单页" not in assert_texts
    assert "邮箱地址" not in assert_texts


def test_assert_texts_length_caps(recorder_page: Page) -> None:
    """用例 15：一次新增 10 条超长文本时，差集最多 8 条、每条截断到 120 字符。"""
    recorder_page.click("#load-news")
    wait_for_snapshot(recorder_page)

    clicks = events_of_type(read_events(recorder_page), "click")
    assert len(clicks) == 1
    assert_texts = clicks[0]["dom_snapshot"]["assert_texts"]

    assert len(assert_texts) == 8
    assert all(len(text) == 120 for text in assert_texts)
    assert len(set(assert_texts)) == 8
    assert assert_texts[0].startswith("公告条目 1：")
    assert assert_texts[7].startswith("公告条目 8：")


def test_assert_texts_attributed_to_latest_event(recorder_page: Page) -> None:
    """回归（2026-09-21 集成冒烟发现的归因缺陷）：fill 不触发 blur，input 事件在
    下一次点击的 focusout 时定稿入队（seq 先于 click），而快照在 click 揭示文本之后
    才拍摄——文本必须归给最新的 click 事件；归给先入队的 input 会让 Then 出现在
    状态变化之前，重放必然假失败。"""
    recorder_page.fill("#email", "user@example.com")
    recorder_page.click("#show-result")  # input 在此 click 的 focusout 定稿，reveal 亦由它引发
    wait_for_snapshot(recorder_page)

    events = read_events(recorder_page)
    input_events = events_of_type(events, "input")
    clicks = events_of_type(events, "click")
    assert len(input_events) == 1 and len(clicks) == 1
    assert input_events[0]["seq"] < clicks[0]["seq"]
    assert "订单已提交成功" in clicks[0]["dom_snapshot"]["assert_texts"]
    assert "订单已提交成功" not in input_events[0]["dom_snapshot"]["assert_texts"]
