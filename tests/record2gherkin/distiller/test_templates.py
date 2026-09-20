"""Group A — rule mapping (spec §6 cases 1-17).

Every case renders the deterministic skeleton through the public API (``polisher=None``).
"""

from __future__ import annotations

import re

import pytest
from record2gherkin.distiller import DistillResult, distill_events
from tests.record2gherkin.distiller.conftest import (
    make_event,
    make_flow,
    single_step,
    skeleton_of,
    step_lines,
)


def test_first_navigate_is_given() -> None:
    """Case 1: a single navigate event renders the Given line."""
    events = make_flow([make_event(1, "navigate", url="https://staging.example.com/login", page_title="Login")])

    assert single_step(skeleton_of(events)) == 'Given I am on the page "https://staging.example.com/login"'


def test_second_navigate_is_when() -> None:
    """Case 2: subsequent navigates render as When steps."""
    events = make_flow(
        [
            make_event(1, "navigate", url="https://staging.example.com/login", page_title="Login"),
            make_event(2, "navigate", url="https://staging.example.com/dashboard", page_title="Dashboard"),
        ]
    )

    lines = step_lines(skeleton_of(events))

    assert lines[0] == 'Given I am on the page "https://staging.example.com/login"'
    assert lines[1] == 'When I navigate to "https://staging.example.com/dashboard"'


def test_click_uses_name_and_role() -> None:
    """Case 3: click hint comes from ``target.name``, role from ``target.role``."""
    events = make_flow([make_event(1, "click", target={"tag": "button", "role": "button", "name": "提交订单"})])

    assert single_step(skeleton_of(events)) == 'When I click on the "提交订单" button'


def test_click_role_fallback_to_tag() -> None:
    """Case 4: missing role falls back to ``target.tag``."""
    events = make_flow([make_event(1, "click", target={"tag": "div", "name": "x"})])

    assert single_step(skeleton_of(events)) == 'When I click on the "x" div'


def test_click_hint_fallback_chain() -> None:
    """Case 5: hint falls back to ``target.form_label`` when ``target.name`` is missing."""
    events = make_flow([make_event(1, "click", target={"form_label": "搜索"})])

    assert single_step(skeleton_of(events)) == 'When I click on the "搜索" element'


def test_click_ordinal_appended() -> None:
    """Case 6: ``ordinal > 1`` is appended as ``(occurrence n)``; ``ordinal == 1`` is not."""
    duplicated = make_flow([make_event(1, "click", target={"name": "Delete", "role": "button", "ordinal": 2})])
    unique = make_flow([make_event(1, "click", target={"name": "Delete", "role": "button", "ordinal": 1})])

    assert single_step(skeleton_of(duplicated)) == 'When I click on the "Delete" button (occurrence 2)'
    assert single_step(skeleton_of(unique)) == 'When I click on the "Delete" button'


def test_input_uses_form_label() -> None:
    """Case 7: input hint prefers ``target.form_label``."""
    events = make_flow([make_event(1, "input", target={"tag": "input", "role": "textbox", "name": "Search products", "form_label": "搜索"}, value="wireless headphones")])

    assert single_step(skeleton_of(events)) == 'When I enter "wireless headphones" in the "搜索" field'


def test_input_hint_fallback_to_name() -> None:
    """Case 8: input hint falls back to ``target.name``."""
    events = make_flow([make_event(1, "input", target={"tag": "input", "role": "textbox", "name": "email"}, value="qa@example.com")])

    assert single_step(skeleton_of(events)) == 'When I enter "qa@example.com" in the "email" field'


def test_masked_input_becomes_placeholder() -> None:
    """Case 9: a masked value becomes a ``{{TEST_DATA:password_<seq>}}`` placeholder."""
    events = make_flow([make_event(7, "input", target={"tag": "input", "role": "textbox", "form_label": "Password"}, value="<masked>")])

    step = single_step(skeleton_of(events))

    assert step == 'When I enter "{{TEST_DATA:password_7}}" in the "Password" field'
    assert "<masked>" not in skeleton_of(events)


def test_masked_input_filled_from_test_data() -> None:
    """Case 10: caller-provided test data replaces the placeholder with the real value."""
    events = make_flow([make_event(7, "input", target={"tag": "input", "role": "textbox", "form_label": "Password"}, value="<masked>")])

    step = single_step(skeleton_of(events, test_data_values={"password_7": "s3cret"}))

    assert step == 'When I enter "s3cret" in the "Password" field'


def test_select_skeleton() -> None:
    """Case 11: select template with value and hint."""
    events = make_flow([make_event(1, "select", target={"tag": "select", "role": "combobox", "name": "Color"}, value="Blue")])

    assert single_step(skeleton_of(events)) == 'When I select "Blue" from the "Color" dropdown'


def test_submit_skeleton() -> None:
    """Case 12: submit template (spec §2.2 supplementary definition)."""
    events = make_flow([make_event(1, "submit", target={"tag": "form", "role": "generic", "name": "提交订单"})])

    assert single_step(skeleton_of(events)) == 'When I submit the "提交订单" form'


def test_click_then_submit_dedupes_submit_step() -> None:
    """P0-1 (spec §2.2 dedup rule): a submit right after a click is the click's twin - only the click step stays.

    The recorder emits click + submit for one submit-button press (recorder spec §4.5); the click step
    carries the button's name hint, so it is the one that survives.
    """
    events = make_flow(
        [
            make_event(1, "click", target={"tag": "button", "role": "button", "name": "提交订单"}),
            make_event(2, "submit", target={"tag": "form", "role": "generic", "name": ""}),
        ]
    )

    lines = step_lines(skeleton_of(events))

    assert lines == ['When I click on the "提交订单" button']
    assert all("submit" not in line for line in lines)


def test_deduped_submit_keeps_its_assertions_after_click() -> None:
    """P0-1 (spec §2.2 dedup rule): dropping the submit step must not drop its snapshot assertions."""
    events = make_flow(
        [
            make_event(1, "navigate", url="https://shop.example.com/checkout", page_title="Checkout"),
            make_event(2, "click", target={"tag": "button", "role": "button", "name": "提交订单"}, assert_texts=["订单已提交"]),
            make_event(3, "submit", target={"tag": "form", "role": "generic", "name": ""}, assert_texts=["Order placed successfully"]),
        ]
    )

    assert step_lines(skeleton_of(events)) == [
        'Given I am on the page "https://shop.example.com/checkout"',
        'When I click on the "提交订单" button',
        'Then I should see "订单已提交"',
        'Then I should see "Order placed successfully"',
    ]


def test_standalone_submit_keeps_its_step() -> None:
    """P0-1 (spec §2.2 dedup rule): a submit without a click predecessor (e.g. Enter-key submit) still renders."""
    events = make_flow(
        [
            make_event(1, "input", target={"tag": "input", "role": "textbox", "form_label": "Search"}, value="wireless headphones"),
            make_event(2, "submit", target={"tag": "form", "role": "generic", "name": "Search"}),
        ]
    )

    assert step_lines(skeleton_of(events)) == [
        'When I enter "wireless headphones" in the "Search" field',
        'When I submit the "Search" form',
    ]


def test_assert_texts_become_then_steps() -> None:
    """Case 13: every snapshot text becomes one Then step, in order, right after its event."""
    events = make_flow(
        [
            make_event(1, "navigate", url="https://staging.example.com/search", page_title="Search"),
            make_event(2, "click", target={"name": "Search", "role": "button"}, assert_texts=["first result", "second result"]),
        ]
    )

    assert step_lines(skeleton_of(events)) == [
        'Given I am on the page "https://staging.example.com/search"',
        'When I click on the "Search" button',
        'Then I should see "first result"',
        'Then I should see "second result"',
    ]


def test_assert_texts_deduped() -> None:
    """Case 14: identical assertion texts are emitted once, at their first occurrence."""
    events = make_flow(
        [
            make_event(1, "click", target={"name": "Save", "role": "button"}, assert_texts=["Saved"]),
            make_event(2, "click", target={"name": "Publish", "role": "button"}, assert_texts=["Saved", "Published"]),
        ]
    )

    lines = step_lines(skeleton_of(events))

    assert lines == [
        'When I click on the "Save" button',
        'Then I should see "Saved"',
        'When I click on the "Publish" button',
        'Then I should see "Published"',
    ]
    assert sum(1 for line in lines if line == 'Then I should see "Saved"') == 1


def test_unknown_event_type_skipped() -> None:
    """Case 15: unknown event types are skipped and reported as warnings."""
    events = make_flow(
        [
            make_event(1, "hover", target={"tag": "div", "role": "generic", "name": "Tooltip"}),
            make_event(2, "click", target={"name": "Continue", "role": "button"}),
        ]
    )

    result = distill_events(events)

    assert step_lines(result.skeleton_text) == ['When I click on the "Continue" button']
    assert "unknown_event_type:hover" in result.warnings


def test_empty_events_still_valid() -> None:
    """Case 16: no events still produce a structurally valid Feature/Scenario pair."""
    result = distill_events(make_flow([]))

    assert result.skeleton_text.count("Feature:") == 1
    assert result.skeleton_text.count("Scenario:") == 1
    assert step_lines(result.skeleton_text) == []
    assert "empty_events" in result.warnings


def test_feature_scenario_naming_rule() -> None:
    """Case 17: Feature uses the session origin, Scenario the first event's page title."""
    events = make_flow(
        [
            make_event(1, "navigate", url="https://staging.example.com/search", page_title="  Staging   Store  "),
            make_event(2, "click", target={"name": "Go", "role": "button"}, page_title="Other page"),
        ],
        origin="https://staging.example.com",
    )
    text = skeleton_of(events)

    assert "Feature: Recorded flow on https_staging_example_com" in text
    assert "Scenario: Staging Store" in text


def test_title_sanitized_to_filename_safe() -> None:
    """回归（pilot002 复盘，阶段 1 分析 P1-4）：标题会被上游用作 JUnit 文件名，
    含 ``://`` 的 origin 会产生 ``//`` 被当作目录分隔符导致写文件失败。标题必须文件名安全。"""
    events = make_flow(
        [
            make_event(1, "navigate", url="http://127.0.0.1:8461/", page_title="MiniShop 商城"),
            make_event(2, "click", target={"name": "Go", "role": "button"}),
        ],
        origin="http://127.0.0.1:8461",
    )
    text = skeleton_of(events)

    assert "Feature: Recorded flow on http_127_0_0_1_8461" in text
    assert re.search(r"[^\w\- \u4e00-\u9fff]", text.splitlines()[0].removeprefix("Feature: ")) is None


def test_naming_rule_defaults_without_origin_and_title() -> None:
    """Spec §2.1 defaults: no origin / no page title."""
    events = {"session": {}, "events": [make_event(1, "click", target={"name": "Go", "role": "button"}, page_title="")]}
    text = skeleton_of(events)

    assert "Feature: Recorded flow\n" in text
    assert "Scenario: Recorded scenario" in text


def test_first_navigate_is_given_even_after_other_events() -> None:
    """Spec §2.2: the *first navigate* (not the first event) renders the Given line."""
    events = make_flow(
        [
            make_event(1, "click", target={"name": "Cookie banner", "role": "button"}),
            make_event(2, "navigate", url="https://staging.example.com/search", page_title="Search"),
        ]
    )

    lines = step_lines(skeleton_of(events))

    assert lines[0] == 'When I click on the "Cookie banner" button'
    assert lines[1] == 'Given I am on the page "https://staging.example.com/search"'


def test_navigate_without_url_is_skipped_with_warning() -> None:
    """Spec §2.2: a navigate without URL and without session origin is skipped with a warning."""
    events = {"session": {}, "events": [make_event(1, "navigate", url="", page_title="Broken")]}
    result = distill_events(events)

    assert step_lines(result.skeleton_text) == []
    assert "missing_url:1" in result.warnings


def test_events_are_sorted_by_seq_before_rendering() -> None:
    """Spec §1.1: events are rendered in ``seq`` order regardless of array order."""
    events = make_flow(
        [
            make_event(3, "click", target={"name": "Third", "role": "button"}),
            make_event(1, "navigate", url="https://staging.example.com/first", page_title="First"),
            make_event(2, "click", target={"name": "Second", "role": "button"}),
        ]
    )

    assert step_lines(skeleton_of(events)) == [
        'Given I am on the page "https://staging.example.com/first"',
        'When I click on the "Second" button',
        'When I click on the "Third" button',
    ]


def test_skeleton_build_is_deterministic_and_never_raises() -> None:
    """Spec §2.5 invariant: rendering never raises, and produces the same text for the same input."""
    payloads = [
        [{"seq": 1, "type": "click", "target": None}],
        [{"type": "input", "value": ""}],
        [{"seq": 2, "type": "select", "value": None, "target": {"tag": "select"}}],
        [{"seq": 1, "type": "submit", "target": {"tag": "form", "name": ""}}],
        [{"seq": 1, "type": "click", "target": {"name": "x", "role": "button", "ordinal": 1}}],
        ["not-an-event-dict"],
        None,
    ]

    for payload in payloads:
        result = distill_events(payload)

        assert isinstance(result, DistillResult)
        assert result.skeleton_text == distill_events(payload).skeleton_text
