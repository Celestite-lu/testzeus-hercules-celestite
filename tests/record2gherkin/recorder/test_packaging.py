"""spec 7 用例 20：bookmarklet 打包产物校验（含 MVP 流程的 schema 复用校验）。"""

from __future__ import annotations

import subprocess
import sys
import urllib.parse

from playwright.sync_api import Browser
from tests.record2gherkin.recorder._helpers import (
    BOOKMARKLET_PATH,
    BUILD_SCRIPT_PATH,
    DEMO_FORM_PATH,
    RECORDER_SOURCE_PATH,
    REPO_ROOT,
    validate_event_stream,
    wait_for_snapshot,
)

JAVASCRIPT_PREFIX = "javascript:"


def test_bookmarklet_build_output(browser: Browser) -> None:
    """用例 20：build_bookmarklet.py 产物可解码、可执行，且跑通完整流程后仍满足 schema。"""
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"打包脚本失败: {result.stderr}"
    assert "bookmarklet.txt" in result.stdout

    payload_text = BOOKMARKLET_PATH.read_text(encoding="utf-8")
    assert payload_text.startswith(JAVASCRIPT_PREFIX)
    assert payload_text[: len(JAVASCRIPT_PREFIX)] == JAVASCRIPT_PREFIX

    source = RECORDER_SOURCE_PATH.read_text(encoding="utf-8")
    decoded = urllib.parse.unquote(payload_text[len(JAVASCRIPT_PREFIX) :])
    assert decoded == source  # recorder.js 已是 IIFE，原样嵌入

    page = browser.new_page()
    page.goto(DEMO_FORM_PATH.as_uri())
    page.evaluate(decoded)
    assert page.evaluate("() => typeof R2GRecorder") == "object"
    assert page.evaluate("() => R2GRecorder.start()") is True

    email = page.locator("#email")
    email.fill("bookmarklet@example.com")
    email.blur()
    page.select_option("#shipping", "express")
    page.locator("#newsletter").check()
    page.click("#submit-btn")
    wait_for_snapshot(page)

    recording = page.evaluate("() => JSON.parse(R2GRecorder.getJSON())")
    validate_event_stream(recording)
    types = [event["type"] for event in recording["events"]]
    assert types[0] == "navigate"
    assert {"input", "select", "click", "submit"} <= set(types)
    page.close()
