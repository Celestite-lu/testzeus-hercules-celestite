"""spec 7 用例 5 / 6 / 7 / 8：input 定稿制、未修改不记录、密码掩码、checkbox 值。"""

from __future__ import annotations

from playwright.sync_api import Page
from tests.record2gherkin.recorder._helpers import (
    events_of_type,
    read_events,
    read_recording,
    wait_for_snapshot,
)


def test_input_records_final_value_once(recorder_page: Page) -> None:
    """用例 5：一次输入过程（多按键/多次赋值）只记 1 条 input，value 为定稿值。

    附带验证 spec 2.2 值规则：上限 500 字符、去首尾换行且保留内部换行（textarea），
    以及事件委托对动态插入元素的天然覆盖。
    """
    email = recorder_page.locator("#email")
    email.fill("alpha")
    email.fill("alpha beta")
    email.fill("alpha beta gamma")
    email.blur()

    coupon = recorder_page.locator("#coupon")
    coupon.fill("x" * 600)
    coupon.blur()

    recorder_page.evaluate("""() => {
        const area = document.createElement("textarea");
        area.id = "note";
        area.setAttribute("aria-label", "备注");
        document.body.appendChild(area);
    }""")
    recorder_page.locator("#note").focus()
    recorder_page.evaluate("() => { document.querySelector('#note').value = '\\n第一行\\n第二行\\n'; }")
    recorder_page.locator("#note").blur()

    wait_for_snapshot(recorder_page)
    inputs = events_of_type(read_events(recorder_page), "input")
    assert len(inputs) == 3

    assert inputs[0]["value"] == "alpha beta gamma"
    assert inputs[0]["target"]["name"] == "邮箱地址"
    assert inputs[0]["target"]["tag"] == "input"
    assert inputs[0]["target"]["role"] == "textbox"
    assert inputs[0]["target"]["testid"] == "email-input"
    assert inputs[0]["target"]["id"] == "email"
    assert inputs[0]["target"]["form_label"] == "邮箱地址"
    assert inputs[0]["target"]["ordinal"] is None

    assert inputs[1]["target"]["name"] == "优惠券码"
    assert inputs[1]["value"] == "x" * 500

    assert inputs[2]["target"]["tag"] == "textarea"
    assert inputs[2]["target"]["name"] == "备注"
    assert inputs[2]["value"] == "第一行\n第二行"


def test_input_unchanged_value_not_recorded(recorder_page: Page) -> None:
    """用例 6：focus 后不改值直接 blur 不记录；改回原值同样不记录。"""
    search = recorder_page.locator("#search")
    search.focus()
    search.blur()

    email = recorder_page.locator("#email")
    email.fill("temporary")
    email.fill("")
    email.blur()

    wait_for_snapshot(recorder_page)
    assert events_of_type(read_events(recorder_page), "input") == []


def test_password_masked(recorder_page: Page) -> None:
    """用例 7：密码框记 1 条 input，value 恒为 <masked>，真实值不入流。"""
    password = recorder_page.locator("#password")
    password.fill("s3cr3t-p@ss")
    password.blur()

    wait_for_snapshot(recorder_page)
    json_text = recorder_page.evaluate("() => R2GRecorder.getJSON()")
    inputs = [event for event in read_recording(recorder_page)["events"] if event["type"] == "input"]

    assert len(inputs) == 1
    assert inputs[0]["value"] == "<masked>"
    assert inputs[0]["target"]["name"] == "密码"
    assert inputs[0]["target"]["form_label"] == "密码"
    assert inputs[0]["target"]["role"] == "textbox"
    assert "s3cr3t-p@ss" not in json_text


def test_checkbox_change_value(recorder_page: Page) -> None:
    """用例 8：勾选/取消 checkbox 各记 1 条 input，value 为 true/false，role=checkbox。"""
    checkbox = recorder_page.locator("#newsletter")
    checkbox.check()
    checkbox.uncheck()

    wait_for_snapshot(recorder_page)
    inputs = events_of_type(read_events(recorder_page), "input")

    assert [event["value"] for event in inputs] == ["true", "false"]
    for event in inputs:
        assert event["target"]["tag"] == "input"
        assert event["target"]["role"] == "checkbox"
        assert event["target"]["name"] == "★ 订阅邮件"
        assert event["target"]["id"] == "newsletter"
