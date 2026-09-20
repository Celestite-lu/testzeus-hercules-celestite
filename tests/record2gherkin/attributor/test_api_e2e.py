"""Group D (part 2) — public API and end-to-end shapes (spec §7 cases 39-42)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from record2gherkin.attributor.api import attribute_case, attribute_run
from record2gherkin.attributor.evidence import (
    KIND_SCREENSHOT,
    normalize_whitespace,
    resolve_reference,
)
from record2gherkin.attributor.report import AttributionResult
from record2gherkin.attributor.rules import SUGGESTION_TEMPLATES
from tests.record2gherkin.attributor.conftest import (
    FakeAnalyzer,
    case,
    junit_xml,
    load_cases,
    message,
    output,
    text_ref,
    thoughts_payload,
)

if TYPE_CHECKING:  # runtime import would make pytest collect the dataclass as a test class
    from record2gherkin.attributor.evidence import TestCaseEvidence

SUBMIT_FAIL = '[ERROR] browser_nav_agent max nav rounds (50) reached before ##TERMINATE TASK##. Last assistant response: Element with selector: "Submit" not found'
CHECKOUT_FEATURE = 'Feature: Checkout\n\n  Scenario: Checkout with a saved card\n    Given the user is on the cart page\n    When the user clicks "Submit"\n    Then the order is confirmed\n'
ASSERT_SUMMARY = "EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item"


def assert_citations_resolvable(evidence: TestCaseEvidence, result: AttributionResult) -> None:
    """The core "never fabricate" invariant: every reported citation re-resolves against the artifacts."""
    assert result.evidence, "a reported result always carries evidence"
    for entry in result.evidence:
        assert entry.reference and entry.excerpt
        referent = resolve_reference(evidence, entry.reference)
        assert referent is not None, f"unresolvable citation {entry.kind} {entry.reference}"
        if entry.kind != KIND_SCREENSHOT:
            assert normalize_whitespace(entry.excerpt) in normalize_whitespace(referent)


def test_end_to_end_submit_double_step_rot(run_dir_factory) -> None:
    """Case 39: the submit-step-after-redirect shape (phase1 P0-1) comes out as test_rot with advice."""
    run_dir = run_dir_factory(
        cases=[case("Checkout with a saved card", failure=SUBMIT_FAIL, final_response="the order was not confirmed")],
        thoughts=thoughts_payload(
            planner=[
                message("human", "Task: click Submit and assert the confirmation page"),
                message("ai", "browser_nav_agent: opened the cart and clicked Proceed to checkout"),
                message("human", "browser_nav_agent: the page redirected to the payment step"),
                message("ai", 'browser_nav_agent: Element with selector: "Submit" not found on the payment step'),
            ]
        ),
        screenshots=["click_using_selector_start_1695000000000000001.png", "click_using_selector_end_1695000000000000002.png"],
        feature_text=CHECKOUT_FEATURE,
    )
    results = attribute_run(str(run_dir))

    assert len(results) == 1
    result = results[0]
    assert result.category == "test_rot"
    assert result.decided_by == "rule"
    assert result.rule_signature == "nav_max_rounds"
    assert result.confidence == 0.75
    assert result.alternative_hypotheses
    assert result.suggestion == SUGGESTION_TEMPLATES["test_rot"]
    assert [entry.reference for entry in result.evidence] == ["junit_failure", "thoughts:3"]
    assert "Element with selector" in result.evidence[1].excerpt

    evidence = load_cases(run_dir)[0]
    assert_citations_resolvable(evidence, result)
    markdown = result.to_markdown()
    assert "## 建议" in markdown and SUGGESTION_TEMPLATES["test_rot"] in markdown
    assert "click_using_selector_end_1695000000000000002.png" in markdown  # appendix screenshot listing


def test_end_to_end_assert_mismatch_fake_llm(run_dir_factory) -> None:
    """Case 40: the silent mis-click shape goes through the LLM layer and keeps a text citation."""
    run_dir = run_dir_factory(
        cases=[case("Checkout with a saved card", failure=ASSERT_SUMMARY, final_response="the order was not placed")],
        thoughts=thoughts_payload(
            planner=[
                message("human", "Task: place the order"),
                message("ai", "browser_nav_agent: I clicked the Save address button because the DOM listed it first, the step was recorded as successful"),
                message("human", "browser_nav_agent: the cart still shows the item"),
            ]
        ),
        feature_text=CHECKOUT_FEATURE,
    )
    analyzer = FakeAnalyzer(
        builder=lambda request: output(
            category="product_bug",
            confidence=0.62,
            summary="agent 点击了 Save address 而非 Place order，断言因此不成立",
            suggestion="复核点击目标的定位策略",
            evidence_refs=[text_ref(request)],
        )
    )
    results = attribute_run(str(run_dir), analyzer=analyzer)

    assert len(results) == 1
    result = results[0]
    assert result.rule_signature == "assertion_mismatch"
    assert result.decided_by == "llm"
    assert result.category == "product_bug"
    assert result.confidence == 0.62
    assert result.llm_summary and "Save address" in result.llm_summary
    assert result.suggestion == "复核点击目标的定位策略"
    assert result.llm_evidence_refs == [text_ref(analyzer.calls[0])]
    assert any(entry.kind == "junit_failure" for entry in result.evidence)

    evidence = load_cases(run_dir)[0]
    assert_citations_resolvable(evidence, result)
    markdown = result.to_markdown()
    assert "## LLM 分析" in markdown and result.llm_summary in markdown
    assert result.to_dict()["notes"]["alternative_hypotheses"] == []


def test_deterministic_without_llm(sample_run_dir) -> None:
    """Case 41: the same directory attributed twice with ``analyzer=None`` is byte-identical."""
    first = attribute_run(str(sample_run_dir))
    second = attribute_run(str(sample_run_dir))

    assert len(first) == len(second) == 1
    assert first[0].to_json() == second[0].to_json()
    assert first[0].to_markdown() == second[0].to_markdown()
    assert first[0].category == "test_rot"
    assert_citations_resolvable(load_cases(sample_run_dir)[0], first[0])


def test_attribute_run_filters_passed(run_dir_factory) -> None:
    """Case 42: only failing testcases produce results."""
    run_dir = run_dir_factory(
        cases=[
            case("green scenario", failure=None, final_response="all good"),
            case("red scenario", failure="Max planner rounds exceeded."),
        ],
        thoughts=thoughts_payload(planner=[message("ai", "planning")]),
    )
    results = attribute_run(str(run_dir))

    assert len(results) == 1
    assert results[0].scenario == "red scenario"
    assert results[0].category == "agent_limit"
    assert results[0].rule_signature == "planner_max_rounds"


def test_attribute_case_not_a_failure(run_dir_factory) -> None:
    """§2.1: a passing testcase short-circuits to inconclusive + ``not_a_failure`` (never the rule layer)."""
    run_dir = run_dir_factory(cases=[case("green scenario", failure=None, final_response="all good")], thoughts=thoughts_payload(planner=[message("ai", "planning")]))
    evidence = load_cases(run_dir)[0]
    result = attribute_case(evidence, analyzer=None)

    assert result.category == "inconclusive"
    assert result.rule_signature is None
    assert "not_a_failure" in result.warnings
    assert result.suggestion == SUGGESTION_TEMPLATES["inconclusive"]
    assert result.evidence == []


def test_attribute_run_with_explicit_junit_path(run_dir_factory, tmp_path) -> None:
    """``junit_path`` overrides the newest-XML discovery (spec §5)."""
    run_dir = run_dir_factory(cases=[case("red scenario", failure="Max planner rounds exceeded.")], junit_rel="output/primary.xml")
    elsewhere = tmp_path / "elsewhere.xml"
    elsewhere.write_text(junit_xml([case("other scenario", failure="Max planner rounds exceeded.")]), encoding="utf-8")

    results = attribute_run(str(run_dir), junit_path=str(elsewhere))

    assert [result.scenario for result in results] == ["other scenario"]


def test_attribute_run_picks_newest_junit(run_dir_factory) -> None:
    """``junit_path=None`` takes the newest XML below the run dir (spec §5)."""
    run_dir = run_dir_factory(cases=[case("old scenario", failure="Max planner rounds exceeded.")], junit_rel="output/old.xml")
    newer = run_dir / "output" / "new.xml"
    newer.write_text(junit_xml([case("new scenario", failure="Max planner rounds exceeded.")]), encoding="utf-8")
    os.utime(newer, (time.time() + 10, time.time() + 10))

    results = attribute_run(str(run_dir))

    assert [result.scenario for result in results] == ["new scenario"]
