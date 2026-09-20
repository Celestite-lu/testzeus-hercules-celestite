"""D 组补充：真浏览器行为验证（本机回环，离线）。

覆盖 review 必改 2 / spec §12 的核心风险：M3 把 id/class/data-testid 全部随机化之后，页面**自身**
的 JS 行为必须仍然生效（搜索出结果、视图切换、提交出结果）。这些用例用语义定位（label/role/文本，
与 baseline 的属性 selector 口径无关）驱动页面，是 §2.1「内联 JS 与 HTML 同源」总则的浏览器级证据；
字符串级证据在 ``test_demo_app.py::test_a12_*``。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from record2gherkin.evaluation.demo_app import (
    SAMPLE_ADDRESS,
    SAMPLE_EMAIL,
    SAMPLE_NAME,
    SEARCH_QUERY,
)
from record2gherkin.evaluation.demo_server import DemoServer

pytestmark = pytest.mark.skipif(__import__("os").environ.get("SKIP_BROWSER_TESTS") == "1", reason="browser tests disabled via SKIP_BROWSER_TESTS=1")


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture()
def demo_http_server() -> Iterator[DemoServer]:
    server = DemoServer(port=0)
    server.start(background=True)
    try:
        yield server
    finally:
        server.stop()


def _page(browser: Browser, server: DemoServer, mutation: str, seed: int) -> Page:
    server.state.set(mutation, seed)
    page = browser.new_page()
    page.set_default_timeout(5000)
    page.goto(f"{server.base_url}/", wait_until="load")
    return page


@pytest.mark.parametrize("mutation,seed", [("M0", 0), ("M3", 42)])
def test_search_behaviour_works_after_mutation(browser: Browser, demo_http_server: DemoServer, mutation: str, seed: int) -> None:
    """M0/M3：搜索按钮真的会过滤卡片并更新状态文案（M3 下 id 全变，语义定位仍可用）。"""
    page = _page(browser, demo_http_server, mutation, seed)
    try:
        page.get_by_label("搜索商品").fill(SEARCH_QUERY)
        page.get_by_role("button", name="搜索").click()
        page.get_by_text("找到 1 件商品").wait_for(state="visible")
        # 未命中的卡片整卡隐藏（含名称/价格/按钮），命中的卡片可见
        assert page.get_by_text("桌面台灯", exact=True).is_visible()
        assert page.get_by_text("陶瓷马克杯", exact=True).is_visible() is False
        assert "陶瓷马克杯" not in page.evaluate("document.body.innerText")
    finally:
        page.close()


@pytest.mark.parametrize("mutation,seed", [("M0", 0), ("M3", 42)])
def test_order_flow_behaviour_works_after_mutation(browser: Browser, demo_http_server: DemoServer, mutation: str, seed: int) -> None:
    """M0/M3：hash 视图切换 + 表单提交 + 数量调整都生效（M3 下按钮 id 不可知）。"""
    page = _page(browser, demo_http_server, mutation, seed)
    try:
        page.get_by_role("link", name="去下单").click()
        page.get_by_label("收货人姓名").fill(SAMPLE_NAME)
        page.get_by_label("联系邮箱").fill(SAMPLE_EMAIL)
        page.get_by_label("收货地址").fill(SAMPLE_ADDRESS)
        page.get_by_label("配送方式").select_option(label="次日达")
        page.get_by_label("接受促销邮件").check()
        page.get_by_role("button", name="+").click()
        page.get_by_text("数量：2").wait_for(state="visible")
        page.get_by_role("button", name="提交订单").click()
        page.get_by_text("下单成功，感谢您的购买！").wait_for(state="visible")
        assert "D20260920-001" in page.inner_text("body")
    finally:
        page.close()


def test_direct_hash_url_opens_order_view(browser: Browser, demo_http_server: DemoServer) -> None:
    """spec §1.1：直接打开 ``#/order`` 也能落到下单视图（蒸馏产物里的 hash 导航必须可直达）。"""
    page = browser.new_page()
    page.set_default_timeout(5000)
    try:
        page.goto(f"{demo_http_server.base_url}/#/order", wait_until="load")
        page.get_by_role("link", name="返回列表").wait_for(state="visible")
        assert page.get_by_role("link", name="去下单").is_visible() is False
    finally:
        page.close()


def test_filter_and_cart_behaviour(browser: Browser, demo_http_server: DemoServer) -> None:
    """补充：筛选（类别+价格）与加入清单计数在浏览器中生效（M0）。"""
    page = _page(browser, demo_http_server, "M0", 0)
    try:
        page.get_by_label("商品类别").select_option(label="数码")
        page.get_by_label("最大价格").fill("300")
        page.get_by_role("button", name="应用筛选").click()
        page.get_by_text("筛选后共 1 件商品").wait_for(state="visible")
        page.get_by_role("button", name="加入清单").nth(0).click()
        page.get_by_text("清单（1）").wait_for(state="visible")
    finally:
        page.close()
