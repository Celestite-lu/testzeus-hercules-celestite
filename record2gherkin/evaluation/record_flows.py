"""D1 recording: replay the six spec §1.3 flows through the recorder and dump schema v1 JSON.

Offline by construction: a local demo server (spec §3) plus an injected copy of
``record2gherkin/recorder/recorder.js``; no LLM, no external site.  Recordings land in
``dev_runs/experiments/recordings/<flow>.json`` and are validated against the spec §1.3 assertion
table, so a recording that silently lost its ``Then`` text fails loudly here instead of poisoning the
downstream feature.

CLI: ``uv run python -m record2gherkin.evaluation.record_flows [--out-dir DIR] [--base-url URL]``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from playwright.sync_api import Page, sync_playwright
from record2gherkin.evaluation.demo_app import (
    MAX_PRICE_INPUT,
    SAMPLE_ADDRESS,
    SAMPLE_EMAIL,
    SAMPLE_NAME,
    SEARCH_QUERY,
    selector_for,
)
from record2gherkin.evaluation.demo_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DemoServer,
    PortInUseError,
)
from record2gherkin.evaluation.sweep import DEFAULT_RECORDINGS_DIR
from testzeus_hercules.utils.logger import logger

RECORDER_JS = Path(__file__).resolve().parents[1] / "recorder" / "recorder.js"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 等待 recorder 的 300ms 延迟快照落地（recorder spec §3）。
SNAPSHOT_WAIT_MS = 900
#: 交互之间的停顿：让每一步的快照（300ms 延迟）落在自己的事件上，而不是被压缩进最后一个事件的
#: 8 条上限。真人录制天然满足该节奏，机器回放必须显式放慢。
STEP_WAIT_MS = 450
#: recorder 事件 schema v1 的字段集（recorder spec §2）。
EVENT_KEYS = frozenset({"seq", "ts", "type", "url", "page_title", "target", "value", "dom_snapshot"})
EVENT_TYPES = frozenset({"navigate", "click", "input", "select", "submit"})

#: spec §1.3 「Then 断言（来自 assert_texts）」逐流程的期望文本（子串匹配）。
EXPECTED_ASSERTIONS: Mapping[str, tuple[str, ...]] = {
    "F1": ("找到 1 件商品",),
    "F2": ("清单（2）",),
    "F3": ("下单成功",),
    "F4": ("筛选后共 1 件商品",),
    "F5": ("共 3 件商品",),
    "F6": ("数量：2", "下单成功"),
}

FLOWS: tuple[str, ...] = tuple(EXPECTED_ASSERTIONS)


class RecordingError(RuntimeError):
    """A recording did not satisfy the spec §1.3 contract."""


def _settle(page: Page, *, step_ms: int = STEP_WAIT_MS) -> None:
    """Pace the replay so the recorder's delayed snapshot fires before the next action."""
    page.wait_for_timeout(step_ms)


def _flow_f1(page: Page, sel: Callable[[str], str]) -> None:
    page.fill(sel("search_input"), SEARCH_QUERY)
    _settle(page)
    page.click(sel("search_button"))


def _flow_f2(page: Page, sel: Callable[[str], str]) -> None:
    page.click(sel("product_1_add"))
    _settle(page)
    page.click(sel("product_3_add"))


def _flow_f3(page: Page, sel: Callable[[str], str]) -> None:
    page.click(sel("nav_order"))
    _settle(page)
    page.fill(sel("name_input"), SAMPLE_NAME)
    _settle(page)
    page.fill(sel("email_input"), SAMPLE_EMAIL)
    _settle(page)
    page.fill(sel("address_input"), SAMPLE_ADDRESS)
    _settle(page)
    page.select_option(sel("shipping_select"), label="次日达")
    _settle(page)
    page.click(sel("promo_checkbox"))
    _settle(page)
    page.click(sel("submit_button"))


def _flow_f4(page: Page, sel: Callable[[str], str]) -> None:
    page.select_option(sel("category_select"), label="数码")
    _settle(page)
    page.fill(sel("max_price_input"), MAX_PRICE_INPUT)
    _settle(page)
    page.click(sel("filter_button"))


def _flow_f5(page: Page, sel: Callable[[str], str]) -> None:
    page.click(sel("nav_order"))
    _settle(page)
    page.click(sel("nav_home"))


def _flow_f6(page: Page, sel: Callable[[str], str]) -> None:
    page.click(sel("nav_order"))
    _settle(page)
    page.click(sel("qty_plus"))
    _settle(page)
    page.click(sel("qty_plus"))
    _settle(page)
    page.click(sel("qty_minus"))
    _settle(page)
    page.click(sel("submit_button"))


FLOW_ACTIONS: Mapping[str, Callable[[Page, Callable[[str], str]], None]] = {
    "F1": _flow_f1,
    "F2": _flow_f2,
    "F3": _flow_f3,
    "F4": _flow_f4,
    "F5": _flow_f5,
    "F6": _flow_f6,
}


def record_flow(page: Page, flow: str, *, base_url: str, recorder_js: str) -> dict[str, Any]:
    """Record one flow in a fresh page state and return the schema v1 payload."""
    page.goto(f"{base_url}/", wait_until="load")
    page.evaluate(recorder_js)
    if page.evaluate("() => R2GRecorder.start()") is not True:
        raise RecordingError(f"{flow}: recorder refused to start")
    try:
        FLOW_ACTIONS[flow](page, selector_for)
        page.wait_for_timeout(SNAPSHOT_WAIT_MS)
    finally:
        page.evaluate("() => R2GRecorder.stop()")
        page.wait_for_timeout(50)
    payload = json.loads(page.evaluate("() => R2GRecorder.getJSON()"))
    _validate_payload(flow, payload)
    return payload


def _validate_payload(flow: str, payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"session", "events"}:
        raise RecordingError(f"{flow}: payload must be a dict with session/events keys")
    events = payload["events"]
    if not isinstance(events, list) or not events:
        raise RecordingError(f"{flow}: no events recorded")
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict) or not EVENT_KEYS <= set(event):
            raise RecordingError(f"{flow}: event {index} misses schema v1 fields")
        if event["type"] not in EVENT_TYPES:
            raise RecordingError(f"{flow}: event {index} has unknown type {event['type']!r}")
    if events[0]["type"] != "navigate":
        raise RecordingError(f"{flow}: first event must be a navigate, got {events[0]['type']!r}")
    captured = [text for event in events for text in event["dom_snapshot"]["assert_texts"]]
    for expected in EXPECTED_ASSERTIONS[flow]:
        if not any(expected in text for text in captured):
            raise RecordingError(f"{flow}: expected assertion {expected!r} missing from captured texts {captured!r}")


def event_types(payload: Mapping[str, Any]) -> list[str]:
    return [str(event["type"]) for event in payload["events"]]


def captured_texts(payload: Mapping[str, Any]) -> list[str]:
    return [text for event in payload["events"] for text in event["dom_snapshot"]["assert_texts"]]


def record_all(
    *,
    out_dir: Path = DEFAULT_RECORDINGS_DIR,
    base_url: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    flows: Sequence[str] = FLOWS,
) -> dict[str, Path]:
    """Record every flow into ``out_dir``; returns ``flow -> json path``."""
    recorder_js = RECORDER_JS.read_text(encoding="utf-8")
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    server = DemoServer(host, port, mutation="M0", seed=0)
    standalone = base_url is None
    if standalone:
        try:
            server.start(background=True)
        except PortInUseError as exc:
            raise RecordingError(str(exc)) from exc
        base_url = server.base_url
    assert base_url is not None

    written: dict[str, Path] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for flow in flows:
                    page = browser.new_page()
                    try:
                        payload = record_flow(page, flow, base_url=base_url, recorder_js=recorder_js)
                    finally:
                        page.close()
                    path = target_dir / f"{flow}.json"
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    written[flow] = path
                    logger.info(
                        "record_flows: %s -> %s (%s events: %s; assert_texts: %s)",
                        flow,
                        path,
                        len(payload["events"]),
                        ",".join(event_types(payload)),
                        " | ".join(captured_texts(payload)),
                    )
            finally:
                browser.close()
    finally:
        if standalone:
            server.stop()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record the six demo flows (spec §1.3)")
    parser.add_argument("--out-dir", default=str(DEFAULT_RECORDINGS_DIR))
    parser.add_argument("--base-url", default=None, help="use an already running demo server instead of starting one")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    try:
        written = record_all(out_dir=Path(args.out_dir), base_url=args.base_url, host=args.host, port=args.port)
    except RecordingError as exc:
        logger.error("record_flows: %s", exc)
        return 2
    logger.info("record_flows: %s recordings written to %s", len(written), args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
