"""Property-selector Playwright baseline (spec §5).

口径声明（最终报告须原样转载）: this baseline imitates a classic record-and-replay UI automation
suite.  It locates elements **only through property selectors** — ``#id`` → ``[data-testid=...]`` →
``.class`` — and never uses ``get_by_role`` / ``get_by_label`` / text locators.  The six flow
functions below mirror spec §1.3 step by step; an assertion failure (or a missing selector) raises
and the driver records ``passed=False``.

Selectors are generated from the M0 registry (:func:`record2gherkin.evaluation.demo_app.REGISTRY`)
rather than hand-copied from page source, so they cannot drift from the app (spec §5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from playwright.sync_api import Page, sync_playwright
from record2gherkin.evaluation.demo_app import (
    MAX_PRICE_INPUT,
    SAMPLE_ADDRESS,
    SAMPLE_EMAIL,
    SAMPLE_NAME,
    SEARCH_QUERY,
    element_keys,
    selector_for,
)
from testzeus_hercules.utils.logger import logger

FLOWS: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6")

#: Default per-action timeout: keeps a M3 cell (every selector misses) fast and deterministic.
DEFAULT_ACTION_TIMEOUT_MS = 2000


class BaselineError(AssertionError):
    """Raised when a baseline step fails (selector miss, wrong text, count mismatch)."""


@dataclass(frozen=True)
class FlowResult:
    """Outcome of one baseline flow run (mirrors the ``results.jsonl`` baseline row)."""

    flow: str
    passed: bool
    duration_s: float
    failure_message: str | None


def expected_selectors(mutation: str = "M0", seed: int = 0) -> dict[str, str]:
    """Property selector of every registry element, generated from the registry (spec §5)."""
    return {key: selector_for(key, mutation, seed) for key in element_keys()}


class _PageDriver:
    """Thin wrapper adding spec §5 semantics (property selectors only) plus hard assertions."""

    def __init__(self, page: Page, base_url: str, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms

    # -- navigation --------------------------------------------------------------------------

    def open(self, path: str = "/") -> None:
        self.page.goto(f"{self.base_url}{path}", wait_until="load")

    # -- interactions ------------------------------------------------------------------------

    def fill(self, selector: str, value: str) -> None:
        self.page.locator(selector).first.fill(value, timeout=self.timeout_ms)

    def click(self, selector: str) -> None:
        self.page.locator(selector).first.click(timeout=self.timeout_ms)

    def select(self, selector: str, label: str) -> None:
        self.page.locator(selector).first.select_option(label=label, timeout=self.timeout_ms)

    def check(self, selector: str) -> None:
        self.page.locator(selector).first.check(timeout=self.timeout_ms)

    # -- assertions --------------------------------------------------------------------------

    def assert_text(self, selector: str, expected: str) -> None:
        """Text-containment assertion on the element's visible text (spec §5: 文本存在性/计数)."""
        element = self.page.locator(selector).first
        element.wait_for(state="visible", timeout=self.timeout_ms)
        actual = element.inner_text(timeout=self.timeout_ms)
        if expected not in actual:
            raise BaselineError(f"expected text {expected!r} in {selector} text {actual!r}")


def _flow_f1(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.fill(sel["search_input"], SEARCH_QUERY)
    driver.click(sel["search_button"])
    driver.assert_text(sel["list_status"], "找到 1 件商品")


def _flow_f2(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.click(sel["product_1_add"])
    driver.click(sel["product_3_add"])
    driver.assert_text(sel["cart_count"], "清单（2）")


def _flow_f3(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.click(sel["nav_order"])
    driver.fill(sel["name_input"], SAMPLE_NAME)
    driver.fill(sel["email_input"], SAMPLE_EMAIL)
    driver.fill(sel["address_input"], SAMPLE_ADDRESS)
    driver.select(sel["shipping_select"], "次日达")
    driver.check(sel["promo_checkbox"])
    driver.click(sel["submit_button"])
    driver.assert_text(sel["order_result"], "下单成功")


def _flow_f4(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.select(sel["category_select"], "数码")
    driver.fill(sel["max_price_input"], MAX_PRICE_INPUT)
    driver.click(sel["filter_button"])
    driver.assert_text(sel["list_status"], "筛选后共 1 件商品")


def _flow_f5(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.click(sel["nav_order"])
    driver.click(sel["nav_home"])
    driver.assert_text(sel["list_status"], "共 3 件商品")


def _flow_f6(driver: _PageDriver, sel: Mapping[str, str]) -> None:
    driver.open("/")
    driver.click(sel["nav_order"])
    driver.click(sel["qty_plus"])
    driver.click(sel["qty_plus"])
    driver.click(sel["qty_minus"])
    driver.assert_text(sel["qty_display"], "数量：2")
    driver.click(sel["submit_button"])
    driver.assert_text(sel["order_result"], "下单成功")


FLOW_FUNCTIONS: dict[str, Callable[[_PageDriver, Mapping[str, str]], None]] = {
    "F1": _flow_f1,
    "F2": _flow_f2,
    "F3": _flow_f3,
    "F4": _flow_f4,
    "F5": _flow_f5,
    "F6": _flow_f6,
}


def run_flow(flow: str, *, base_url: str, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> FlowResult:
    """Run one baseline flow against ``base_url`` with a fresh browser context (spec §5).

    Never raises: a failure (including selectors that all miss under M3) becomes ``passed=False``
    plus the exception summary, matching the "断言失败即失败数据" rule of the experiment.
    """
    if flow not in FLOW_FUNCTIONS:
        raise ValueError(f"unknown flow: {flow!r} (expected one of {FLOWS})")

    selectors = expected_selectors()
    started = time.monotonic()
    failure: str | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout_ms)
            driver = _PageDriver(page, base_url, timeout_ms)
            try:
                FLOW_FUNCTIONS[flow](driver, selectors)
            except Exception as exc:  # noqa: BLE001 - any failure is experimental data
                first_line = str(exc).splitlines()[0] if str(exc) else ""
                failure = f"{type(exc).__name__}: {first_line}"[:500]
                logger.info("baseline: %s failed (%s)", flow, failure)
            finally:
                page.close()
        finally:
            browser.close()
    return FlowResult(flow=flow, passed=failure is None, duration_s=round(time.monotonic() - started, 3), failure_message=failure)


def run_all_flows(*, base_url: str, flows: Sequence[str] = FLOWS, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> list[FlowResult]:
    """Run every baseline flow sequentially (spec §7: baseline runs stay local and cost nothing)."""
    return [run_flow(flow, base_url=base_url, timeout_ms=timeout_ms) for flow in flows]


__all__ = [
    "DEFAULT_ACTION_TIMEOUT_MS",
    "FLOWS",
    "FLOW_FUNCTIONS",
    "BaselineError",
    "FlowResult",
    "expected_selectors",
    "run_all_flows",
    "run_flow",
]
