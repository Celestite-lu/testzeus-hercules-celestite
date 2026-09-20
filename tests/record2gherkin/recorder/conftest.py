"""record2gherkin recorder 测试夹具（spec 7）。

流程与生产注入路径同构：file:// 打开 fixture HTML -> page.evaluate 注入 recorder.js 源码 -> start()。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from tests.record2gherkin.recorder._helpers import DEMO_FORM_PATH, load_recorder_source


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def recorder_page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    page.goto(DEMO_FORM_PATH.as_uri())
    page.evaluate(load_recorder_source())
    assert page.evaluate("() => R2GRecorder.start()") is True
    yield page
    page.close()
