"""Group B — deterministic signature rules (spec §7 cases 12-27)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from record2gherkin.attributor.api import attribute_case
from record2gherkin.attributor.evidence import (
    KIND_FEATURE_FILE,
    KIND_JUNIT_FAILURE,
    KIND_THOUGHTS,
)
from record2gherkin.attributor.llm_layer import build_analysis_request
from record2gherkin.attributor.rules import SUGGESTION_TEMPLATES, apply_rules
from tests.record2gherkin.attributor.conftest import (
    DEFAULT_FEATURE,
    FakeAnalyzer,
    case,
    load_first_case,
    message,
    output,
    rule_outcome,
    text_ref,
    thoughts_payload,
)

NAV_MAX_ROUNDS_FAIL = "[ERROR] browser_nav_agent max nav rounds (50) reached before ##TERMINATE TASK##. Last assistant response: clicked the button"
QUIET_ROUND = "browser_nav_agent reported the step as finished and terminated the task"

SIG_DIR = Path(__file__).resolve().parents[3] / "record2gherkin" / "attributor"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _outcome(run_dir_factory, *, failure: str | None = None, sysout=None, rounds=(), feature_text: str | None = DEFAULT_FEATURE, props=None):
    """Rule outcome for a synthesized single-case run (rounds become planner ``ai`` messages)."""
    payload = thoughts_payload(planner=[message("ai", text) for text in rounds]) if rounds else None
    run_dir = run_dir_factory(cases=[case("signature", failure=failure, sysout=sysout)], thoughts=payload, feature_text=feature_text, props=props)
    return rule_outcome(run_dir)


def test_sig_planner_max_rounds(run_dir_factory) -> None:
    """Case 12: S1 — planner round budget exhausted."""
    outcome = _outcome(run_dir_factory, failure="Max planner rounds exceeded.")
    assert outcome.route == "agent_limit"
    assert outcome.rule_signature == "planner_max_rounds"
    assert outcome.confidence == 0.95
    assert outcome.evidence[0].kind == KIND_JUNIT_FAILURE
    assert outcome.evidence[0].reference == "junit_failure"
    assert "Max planner rounds exceeded" in outcome.evidence[0].excerpt


def test_sig_planner_timeout(run_dir_factory) -> None:
    """Case 13: S2 — both engine timeout shapes hit the same signature."""
    from_fail = _outcome(run_dir_factory, failure="planner_agent LLM call timed out after 60s")
    assert from_fail.route == "agent_limit"
    assert from_fail.rule_signature == "planner_timeout"
    assert from_fail.confidence == 0.90
    assert from_fail.evidence[0].kind == KIND_JUNIT_FAILURE

    from_sysout = _outcome(run_dir_factory, failure="the run terminated", sysout="the planner receives a timely model response was violated")
    assert from_sysout.route == "agent_limit"
    assert from_sysout.rule_signature == "planner_timeout"
    assert from_sysout.evidence[0].kind == "junit_sysout"
    assert from_sysout.evidence[0].reference == "sysout"


def test_sig_nav_max_rounds_plain(run_dir_factory) -> None:
    """Case 14: S3 branch (c) — nav round exhaustion without a secondary signal."""
    outcome = _outcome(run_dir_factory, failure=NAV_MAX_ROUNDS_FAIL, rounds=[QUIET_ROUND])
    assert outcome.route == "agent_limit"
    assert outcome.rule_signature == "nav_max_rounds"
    assert outcome.confidence == 0.90
    assert outcome.evidence[0].reference == "junit_failure"
    assert len(outcome.evidence) == 1


def test_sig_nav_max_rounds_not_found_secondary(run_dir_factory) -> None:
    """Case 15: S3 branch (a) — a not-found round in THOUGHTS turns the exhaustion into test_rot."""
    outcome = _outcome(run_dir_factory, failure=NAV_MAX_ROUNDS_FAIL, rounds=[QUIET_ROUND, 'Element with selector: "提交" not found'])
    assert outcome.route == "test_rot"
    assert outcome.rule_signature == "nav_max_rounds"
    assert outcome.confidence == 0.75
    assert outcome.evidence[0].reference == "junit_failure"
    assert outcome.evidence[1].kind == KIND_THOUGHTS
    assert outcome.evidence[1].reference == "thoughts:1"
    assert outcome.alternative_hypotheses


def test_sig_nav_max_rounds_network_secondary(run_dir_factory) -> None:
    """Case 16: S3 branch (b) — a transport error in THOUGHTS turns the exhaustion into environment."""
    outcome = _outcome(run_dir_factory, failure=NAV_MAX_ROUNDS_FAIL, rounds=[QUIET_ROUND, "net::ERR_NAME_NOT_RESOLVED while opening https://shop.example.com"])
    assert outcome.route == "environment"
    assert outcome.rule_signature == "nav_max_rounds"
    assert outcome.confidence == 0.75
    assert outcome.evidence[1].reference == "thoughts:1"


def test_sig_nav_max_rounds_branch_order(run_dir_factory) -> None:
    """S3 secondary scan order: not-found wins over a transport error (spec §3.2 branch order)."""
    outcome = _outcome(
        run_dir_factory,
        failure=NAV_MAX_ROUNDS_FAIL,
        rounds=["net::ERR_NAME_NOT_RESOLVED while opening the cart", 'Element with selector: "Confirm order" not found'],
    )
    assert outcome.route == "test_rot"
    assert outcome.evidence[1].reference == "thoughts:1"  # the not-found round, not the network one


def test_sig_context_limit(run_dir_factory) -> None:
    """Case 17: S4 — context-window errors are an engine limit."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=["the model call failed: maximum context length exceeded"])
    assert outcome.route == "agent_limit"
    assert outcome.rule_signature == "llm_context_limit"
    assert outcome.confidence == 0.85
    assert outcome.evidence[0].kind == KIND_THOUGHTS
    assert outcome.evidence[0].reference == "thoughts:0"


def test_sig_network_dns(run_dir_factory) -> None:
    """Case 18: S5 — browser-level network failure is an environment problem."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=["page.goto failed: net::ERR_CONNECTION_REFUSED"])
    assert outcome.route == "environment"
    assert outcome.rule_signature == "network_dns"
    assert outcome.confidence == 0.90
    assert outcome.evidence[0].reference == "thoughts:0"


def test_sig_network_dns_first_and_last_round(run_dir_factory) -> None:
    """S5 keeps the first *and* last matching round of THOUGHTS (spec §3.1/§3.2)."""
    outcome = _outcome(
        run_dir_factory,
        failure="run aborted",
        rounds=["net::ERR_CONNECTION_REFUSED on the first try", QUIET_ROUND, "net::ERR_CONNECTION_REFUSED on the retry"],
    )
    assert [entry.reference for entry in outcome.evidence] == ["thoughts:0", "thoughts:2"]
    assert outcome.hit_rounds == [0, 2]


def test_sig_browser_disconnected(run_dir_factory) -> None:
    """Case 19: S6 — browser/CDP disconnect."""
    outcome = _outcome(run_dir_factory, failure="Target page, context or browser has been closed")
    assert outcome.route == "environment"
    assert outcome.rule_signature == "browser_disconnected"
    assert outcome.confidence == 0.80
    assert outcome.evidence[0].reference == "junit_failure"


def test_sig_tool_error_network(run_dir_factory) -> None:
    """Case 20: S7 — tool error plus a network word in the same fragment."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=["[TOOL ERROR] open_url: Navigation timed out after 30000ms"])
    assert outcome.route == "environment"
    assert outcome.rule_signature == "tool_error_network"
    assert outcome.confidence == 0.75
    assert outcome.evidence[0].reference == "thoughts:0"
    assert outcome.alternative_hypotheses


def test_sig_tool_error_without_network_routes_llm(run_dir_factory) -> None:
    """S7 requires the transport word in the same fragment; otherwise S13 routes to the LLM layer."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=["[TOOL ERROR] fill_using_selector: input cannot be filled"])
    assert outcome.route == "needs_llm"
    assert outcome.rule_signature == "generic_tool_error"


def test_sig_element_not_found(run_dir_factory) -> None:
    """Case 21: S8 — not-found element is test_rot with an alternative hypothesis, plus a feature line."""
    feature_text = 'Feature: Search\n\n  Scenario: search\n    Given the user opens the search page\n    When the user clicks "Go"\n    Then results are shown\n'
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=['Element with selector: "Go" not found'], feature_text=feature_text)
    assert outcome.route == "test_rot"
    assert outcome.rule_signature == "element_not_found"
    assert outcome.confidence == 0.70
    assert outcome.alternative_hypotheses
    assert outcome.evidence[0].reference == "thoughts:0"
    feature_refs = [entry for entry in outcome.evidence if entry.kind == KIND_FEATURE_FILE]
    assert len(feature_refs) == 1
    assert feature_refs[0].reference == "feature:5"
    assert '"Go"' in feature_refs[0].excerpt

    # No dedicated entry when the selector never appears in the feature text.
    without_go = _outcome(run_dir_factory, failure="run aborted", rounds=['Element with selector: "Go" not found'], feature_text="Feature: Other\n\n  Scenario: other\n    Then nothing happens\n")
    assert [entry.kind for entry in without_go.evidence] == [KIND_THOUGHTS]


def test_sig_http_404(run_dir_factory) -> None:
    """Case 22: S9 — a 404 status on the referenced resource is (weak) test rot."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=["GET https://shop.example.com/orders status=404 for the order list"])
    assert outcome.route == "test_rot"
    assert outcome.rule_signature == "http_404"
    assert outcome.confidence == 0.65
    assert outcome.evidence[0].reference == "thoughts:0"
    assert outcome.alternative_hypotheses


def test_sig_assert_mismatch_routes_llm(run_dir_factory) -> None:
    """Case 23: S10 — EXPECTED/ACTUAL only routes; the analyzer receives the FAIL text as seed evidence."""
    assert_summary = "EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item"
    run_dir = run_dir_factory(
        cases=[case("silent misclick", failure=assert_summary, final_response="the order was not placed")],
        thoughts=thoughts_payload(planner=[message("ai", "I clicked the wrong button but the step was recorded as successful")]),
    )
    evidence = load_first_case(run_dir)
    outcome = apply_rules(evidence)
    assert outcome.route == "needs_llm"
    assert outcome.rule_signature == "assertion_mismatch"
    assert outcome.confidence is None
    assert outcome.alternative_hypotheses  # three hypotheses seeded (spec §3.2)

    analyzer = FakeAnalyzer(builder=lambda request: output(category="product_bug", confidence=0.6, evidence_refs=[text_ref(request)]))
    result = attribute_case(evidence, analyzer=analyzer)
    assert analyzer.calls, "the analyzer must have been called for a needs_llm route"
    package = build_analysis_request(evidence, outcome).seed_evidence
    assert any(item.kind == KIND_JUNIT_FAILURE and "EXPECTED RESULT" in item.excerpt for item in package)
    assert result.decided_by == "llm"
    assert result.category == "product_bug"


def test_sig_llm_call_error(run_dir_factory) -> None:
    """Case 24: S12 — the agent's model call failed."""
    outcome = _outcome(run_dir_factory, failure="[ERROR] browser_nav_agent LLM error: quota exceeded")
    assert outcome.route == "agent_limit"
    assert outcome.rule_signature == "llm_call_error"
    assert outcome.confidence == 0.80
    assert outcome.evidence[0].reference == "junit_failure"


def test_sig_helper_uncertain_routes_llm(run_dir_factory) -> None:
    """Case 25: S11 — an uncertain/incomplete helper termination routes to the LLM layer."""
    outcome = _outcome(
        run_dir_factory,
        failure="the run stopped early",
        sysout="[browser_nav_agent]: ##TERMINATE TASK## the result is uncertain, the form may be incomplete",
    )
    assert outcome.route == "needs_llm"
    assert outcome.rule_signature == "helper_uncertain"
    assert outcome.evidence[0].kind == "junit_sysout"


def test_precedence_limit_before_mismatch(run_dir_factory) -> None:
    """Case 26: table order is priority — S1 beats S10 on a FAIL that carries both."""
    outcome = _outcome(run_dir_factory, failure="EXPECTED RESULT: done in 5 rounds\nACTUAL RESULT: max planner rounds exceeded.")
    assert outcome.rule_signature == "planner_max_rounds"
    assert outcome.route == "agent_limit"
    assert outcome.confidence == 0.95


def test_precedence_element_not_found_before_generic_tool_error(run_dir_factory) -> None:
    """S8/S13 stay ordered as in the table (a not-found tool failure is test_rot, not a generic tool error)."""
    outcome = _outcome(run_dir_factory, failure="run aborted", rounds=['[TOOL ERROR] click_using_selector: Element with selector: "Pay" not found'])
    assert outcome.rule_signature == "element_not_found"
    assert outcome.route == "test_rot"


def test_fallback_routes_llm(run_dir_factory) -> None:
    """Table last row: no signature -> needs_llm with no signature and no evidence."""
    outcome = _outcome(run_dir_factory, failure="something went wrong", sysout="nothing recognizable", rounds=["plain text"])
    assert outcome.route == "needs_llm"
    assert outcome.rule_signature is None
    assert outcome.confidence is None
    assert outcome.evidence == []


def test_rules_zero_llm_imports() -> None:
    """Case 27: the model-free layer never imports an LLM/network module (spec §8 criterion 2)."""
    banned = ("litellm", "requests", "httpx", "aiohttp", "urllib", "openai")
    for name in ("rules.py", "evidence.py", "report.py"):
        text = (SIG_DIR / name).read_text(encoding="utf-8")
        assert "attributor.llm_layer" not in text, f"{name} must not import the LLM layer"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                lowered = stripped.lower()
                assert not any(token in lowered for token in banned), f"{name}: {stripped}"


def test_rule_excerpt_is_truncated_to_500(run_dir_factory) -> None:
    """§3.2: rule evidence excerpts are the hit line, truncated to 500 characters."""
    long_line = "Max planner rounds exceeded. " + "x" * 900
    outcome = _outcome(run_dir_factory, failure=long_line)
    assert outcome.rule_signature == "planner_max_rounds"
    assert len(outcome.evidence[0].excerpt) == 500
    assert outcome.evidence[0].excerpt.startswith("Max planner rounds exceeded.")


def test_package_import_is_offline() -> None:
    """Importing the package must not pull in the LLM/network wiring (spec §4.1 offline constraint).

    ``DefaultAttributorAnalyzer`` resolves its model lazily, so a plain import (in a fresh interpreter)
    stays free of litellm/langchain/openai imports and of any configuration side effect.
    """
    code = (
        "import sys\n"
        "import record2gherkin.attributor as attributor\n"
        "banned = sorted(m for m in sys.modules if m.split('.')[0] in {'litellm', 'langchain', 'openai', 'httpx', 'urllib3'})\n"
        "assert not banned, banned\n"
        "assert 'testzeus_hercules.utils.litellm_helper' not in sys.modules\n"
        "assert attributor.DefaultAttributorAnalyzer is not None\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, env={**os.environ, "ENABLE_TELEMETRY": "0"})
    assert completed.returncode == 0, completed.stderr


def test_suggestion_templates_cover_categories() -> None:
    """§3.3: one deterministic template per category, and every rule decision uses it."""
    from record2gherkin.attributor.evidence import CATEGORIES

    assert set(SUGGESTION_TEMPLATES) == set(CATEGORIES)
    assert len(set(SUGGESTION_TEMPLATES.values())) == len(CATEGORIES)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("Max planner rounds exceeded.", "agent_limit"),
        ("planner_agent LLM call timed out after 60s", "agent_limit"),
        ("Target page, context or browser has been closed", "environment"),
    ],
)
def test_rules_do_not_need_thoughts(run_dir_factory, failure: str, expected: str) -> None:
    """The rule layer works on FAIL alone (spec §3.1 corpus tolerance)."""
    outcome = _outcome(run_dir_factory, failure=failure, rounds=())
    assert outcome.route == expected
