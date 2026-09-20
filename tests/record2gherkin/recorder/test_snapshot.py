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
