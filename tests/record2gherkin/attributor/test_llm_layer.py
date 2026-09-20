"""Group C — LLM layer, citation back-check and degradation (spec §7 cases 28-36).

Every test drives the layer with :class:`FakeAnalyzer`; no model, no network.
"""

from __future__ import annotations

import asyncio

import pytest
from record2gherkin.attributor.api import attribute_case
from record2gherkin.attributor.evidence import KIND_SCREENSHOT, resolve_reference
from record2gherkin.attributor.llm_layer import (
    MAX_EVIDENCE_ITEMS,
    DefaultAttributorAnalyzer,
    EvidenceItem,
    LlmAnalysisOutput,
    LlmAnalysisRequest,
    backcheck_refs,
    build_analysis_request,
    resolve_needs_llm,
    screenshot_names,
)
from record2gherkin.attributor.rules import SUGGESTION_TEMPLATES, apply_rules
from tests.record2gherkin.attributor.conftest import (
    DEFAULT_FEATURE,
    FakeAnalyzer,
    case,
    load_first_case,
    message,
    output,
    screenshot_ref,
    text_ref,
    thoughts_payload,
)

ASSERT_SUMMARY = "EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item"
MISCLICK_ROUND = "I clicked the wrong button but the step was recorded as successful"


def _routed_case(run_dir_factory, *, rounds=(MISCLICK_ROUND,), failure: str = ASSERT_SUMMARY, screenshots=(), feature_text: str | None = DEFAULT_FEATURE):
    """A ``needs_llm`` case plus its evidence package: ``(evidence, outcome, request)``."""
    run_dir = run_dir_factory(
        cases=[case("silent misclick", failure=failure, final_response="the order was not placed")],
        thoughts=thoughts_payload(planner=[message("ai", text) for text in rounds]),
        screenshots=screenshots,
        feature_text=feature_text,
    )
    evidence = load_first_case(run_dir)
    outcome = apply_rules(evidence)
    assert outcome.route == "needs_llm", "the case must route to the LLM layer"
    return evidence, outcome, build_analysis_request(evidence, outcome)


def test_llm_valid_output_adopted(run_dir_factory) -> None:
    """Case 28: a schema-valid answer citing existing evidence is adopted as-is."""
    evidence, outcome, request = _routed_case(run_dir_factory)
    cited = text_ref(request, position=len(request.seed_evidence) - 1)
    package_item = {item.ref_id: item for item in request.seed_evidence}[cited]
    analyzer = FakeAnalyzer(outputs=[output(category="test_rot", confidence=0.73, summary="the submit step landed on a different page", suggestion="re-record the submit step", evidence_refs=[cited])])
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.decided_by == "llm"
    assert decision.category == "test_rot"
    assert decision.confidence == 0.73
    assert decision.summary == "the submit step landed on a different page"
    assert decision.suggestion == "re-record the submit step"
    assert decision.warnings == []
    assert [entry.reference for entry in decision.evidence] == [package_item.reference]
    assert decision.evidence[0].excerpt == package_item.excerpt


def test_llm_unknown_ref_degrades(run_dir_factory) -> None:
    """Case 29: a fabricated ref id forces inconclusive and keeps the rule-layer seed evidence."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(outputs=[output(category="product_bug", confidence=0.9, evidence_refs=["E99"])])
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.category == "inconclusive"
    assert decision.decided_by == "degraded"
    assert decision.warnings[0].startswith("evidence_backcheck_failed")
    assert "E99" in decision.warnings[0]
    assert decision.confidence == 0.30
    assert decision.suggestion == SUGGESTION_TEMPLATES["inconclusive"]
    assert [entry.reference for entry in decision.evidence] == [entry.reference for entry in outcome.evidence]
    assert outcome.evidence, "the seed evidence of the routing signature must be non-empty here"
    assert len(analyzer.calls) == 2, "one retry with the violation receipt"
    assert "violation: unknown_ref E99" in (analyzer.calls[1].retry_feedback or "")


def test_llm_screenshot_only_citation_degrades(run_dir_factory) -> None:
    """Case 30: screenshots never support a conclusion (spec §4.4-4)."""
    evidence, outcome, request = _routed_case(
        run_dir_factory,
        screenshots=["click_using_selector_start_1695000000000000001.png", "click_using_selector_end_1695000000000000002.png"],
    )
    shot = screenshot_ref(request)
    analyzer = FakeAnalyzer(outputs=[output(category="product_bug", confidence=0.8, evidence_refs=[shot])])
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.category == "inconclusive"
    assert decision.decided_by == "degraded"
    assert decision.warnings[0].startswith("evidence_backcheck_failed")
    assert shot in decision.warnings[0]
    assert len(analyzer.calls) == 2
    assert "insufficient_evidence" in (analyzer.calls[1].retry_feedback or "")


def test_llm_partial_drops_still_adopt(run_dir_factory) -> None:
    """§4.4-3/§4.4-4: a dropped ref is recorded, but a surviving text citation still carries the verdict."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(builder=lambda request: output(category="test_rot", confidence=0.5, evidence_refs=["E99", text_ref(request)]))
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.decided_by == "llm"
    assert decision.category == "test_rot"
    assert decision.warnings == ["evidence_ref_dropped:unknown_ref E99"]
    assert len(decision.evidence) == 1
    assert len(analyzer.calls) == 1, "a rejected ref does not trigger the retry when the verdict still stands"


def test_llm_invalid_then_valid_retry(run_dir_factory) -> None:
    """Case 31: the first answer is rejected on schema, the retry (with receipt) is adopted."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    seen: list[int] = []

    def builder(request: LlmAnalysisRequest) -> LlmAnalysisOutput:
        seen.append(len(seen) + 1)
        if len(seen) == 1:
            return output(category="bug")  # invalid enum value
        return output(category="test_rot", confidence=0.66, summary="second answer", evidence_refs=[text_ref(request)])

    analyzer = FakeAnalyzer(builder=builder)
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.decided_by == "llm"
    assert decision.category == "test_rot"
    assert decision.summary == "second answer"
    assert len(analyzer.calls) == 2
    assert "violation: invalid_category" in (analyzer.calls[1].retry_feedback or "")
    assert analyzer.calls[0].retry_feedback is None


def test_llm_invalid_twice_degrades(run_dir_factory) -> None:
    """Case 32: two schema-invalid answers degrade with ``llm_output_invalid``."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(outputs=[output(category="bug"), output(category="bug")])
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.category == "inconclusive"
    assert decision.decided_by == "degraded"
    assert decision.warnings == ["llm_output_invalid"]
    assert decision.confidence == 0.30
    assert len(analyzer.calls) == 2


def test_llm_exception_degrades(run_dir_factory) -> None:
    """Case 33: two transport failures degrade with the exception class in the warning."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(error=TimeoutError("model did not answer"))
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.category == "inconclusive"
    assert decision.decided_by == "degraded"
    assert decision.warnings == ["llm_error:TimeoutError"]
    assert len(analyzer.calls) == 2


def test_llm_config_error_degrades_without_retry(run_dir_factory) -> None:
    """A broken analyzer configuration is "analyzer unavailable", not a transient call failure (spec §4.5)."""
    from record2gherkin.attributor.evidence import AttributionError

    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(error=AttributionError("no attributor_analyze key"))
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.decided_by == "degraded"
    assert decision.warnings == ["llm_not_configured"]
    assert len(analyzer.calls) == 1


def test_no_analyzer_rules_only(run_dir_factory) -> None:
    """Case 34: ``analyzer=None`` keeps the run deterministic — inconclusive plus ``llm_not_configured``."""
    run_dir = run_dir_factory(
        cases=[case("silent misclick", failure=ASSERT_SUMMARY, final_response="the order was not placed")],
        thoughts=thoughts_payload(planner=[message("ai", MISCLICK_ROUND)]),
    )
    result = attribute_case(load_first_case(run_dir), analyzer=None)

    assert result.category == "inconclusive"
    assert result.decided_by == "degraded"
    assert result.confidence == 0.30
    assert "llm_not_configured" in result.warnings
    assert result.suggestion == SUGGESTION_TEMPLATES["inconclusive"]
    assert result.rule_signature == "assertion_mismatch"
    assert result.evidence, "rule-layer seed evidence is preserved on the degraded path"
    assert result.llm_summary is None


def test_llm_inconclusive_allowed(run_dir_factory) -> None:
    """Case 35: an explicit inconclusive verdict with no refs is adopted (not degraded)."""
    evidence, outcome, _request = _routed_case(run_dir_factory)
    analyzer = FakeAnalyzer(outputs=[output(category="inconclusive", confidence=0.2, summary="not enough signal", evidence_refs=[])])
    decision = asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))

    assert decision.decided_by == "llm"
    assert decision.category == "inconclusive"
    assert decision.summary == "not enough signal"
    assert decision.evidence == []
    assert decision.warnings == []
    assert decision.suggestion == SUGGESTION_TEMPLATES["inconclusive"], "empty suggestions fall back to the template"


def test_backcheck_locator_semantics(run_dir_factory) -> None:
    """Case 36: every §4.4-3 violation kind is produced by the back-checker."""
    evidence, _outcome, _request = _routed_case(run_dir_factory, rounds=["line one\n   line two"])
    items = [
        EvidenceItem(ref_id="E1", kind="thoughts", reference="thoughts:99", excerpt="anything"),
        EvidenceItem(ref_id="E2", kind="feature_file", reference="feature:999", excerpt="anything"),
        EvidenceItem(ref_id="E3", kind="thoughts", reference="thoughts:0", excerpt="not inside that round"),
        EvidenceItem(ref_id="E4", kind="junit_property", reference="property:NoSuchKey", excerpt="anything"),
        EvidenceItem(ref_id="E5", kind="screenshot", reference="screenshot:missing.png", excerpt="missing.png"),
    ]
    request = LlmAnalysisRequest(scenario="s", failure_message="f", final_response=None, seed_evidence=items, screenshot_names=[], feature_excerpt="")
    answer = LlmAnalysisOutput(category="test_rot", confidence=0.5, summary="s", suggestion="", evidence_refs=[item.ref_id for item in items])

    survivors, violations, dropped = backcheck_refs(answer, request, evidence)

    assert survivors == []
    assert violations == ["unresolvable E1", "unresolvable E2", "excerpt_mismatch E3", "unresolvable E4", "unresolvable E5"]
    assert dropped == ["E1", "E2", "E3", "E4", "E5"]


def test_backcheck_whitespace_normalization(run_dir_factory) -> None:
    """§4.4-3 normalization: collapsed whitespace still matches, case differences do not."""
    evidence, _outcome, _request = _routed_case(run_dir_factory, rounds=["line one\n\n   line two ends here"])
    items = [
        EvidenceItem(ref_id="E1", kind="thoughts", reference="thoughts:0", excerpt="line one line two"),
        EvidenceItem(ref_id="E2", kind="thoughts", reference="thoughts:0", excerpt="LINE ONE"),
    ]
    request = LlmAnalysisRequest(scenario="s", failure_message="f", final_response=None, seed_evidence=items, screenshot_names=[], feature_excerpt="")
    answer = LlmAnalysisOutput(category="test_rot", confidence=0.5, summary="s", suggestion="", evidence_refs=["E1", "E2"])

    survivors, violations, _dropped = backcheck_refs(answer, request, evidence)
    assert [item.ref_id for item in survivors] == ["E1"]
    assert violations == ["excerpt_mismatch E2"]


@pytest.mark.parametrize(
    ("reference", "resolvable"),
    [
        ("junit_failure", True),
        ("sysout", True),
        ("sysout:final_response", True),
        ("thoughts:0", True),
        ("thoughts:-1", False),
        ("thoughts:999", False),
        ("thoughts:x", False),
        ("feature:1", True),
        ("feature:0", False),
        ("feature:99999", False),
        ("property:Terminate", False),  # not among the synthesized properties
        ("screenshot:click_using_selector_end_1695000000000000002.png", True),
        ("screenshot:nope.png", False),
        ("something_else", False),
    ],
)
def test_resolve_reference_boundaries(run_dir_factory, reference: str, resolvable: bool) -> None:
    """§6.3 locator grammar: resolvable or explicitly unresolvable (never a silent guess)."""
    run_dir = run_dir_factory(
        cases=[case("locators", failure="boom", final_response="final state text")],
        thoughts=thoughts_payload(planner=[message("ai", "round zero")]),
        screenshots=["click_using_selector_end_1695000000000000002.png"],
    )
    evidence = load_first_case(run_dir)
    resolved = resolve_reference(evidence, reference)
    assert (resolved is not None) == resolvable


def test_evidence_package_is_capped_and_keeps_seed(run_dir_factory) -> None:
    """§4.2-4: over the 30-item cap, context rounds go first, then non-final screenshots; seed stays."""
    shots = [f"tool_{index}_end_1695000000000{index:04d}.png" for index in range(40)]
    rounds = [f"state saved to proofs/stake1/run1/screenshots/{name}" for name in shots]
    rounds.insert(0, MISCLICK_ROUND)
    evidence, _outcome, request = _routed_case(run_dir_factory, rounds=rounds, screenshots=shots)

    refs = [item.ref_id for item in request.seed_evidence]
    assert len(request.seed_evidence) == MAX_EVIDENCE_ITEMS
    assert refs == [f"E{position}" for position in range(1, MAX_EVIDENCE_ITEMS + 1)]
    assert any(item.kind == "junit_failure" and "EXPECTED RESULT" in item.excerpt for item in request.seed_evidence)
    final_names = {name.removesuffix(" [FINAL]") for name in screenshot_names(evidence) if name.endswith("[FINAL]")}
    packaged_shots = {item.reference.removeprefix("screenshot:") for item in request.seed_evidence if item.kind == KIND_SCREENSHOT}
    assert final_names <= packaged_shots


def test_thoughts_excerpt_is_pure_truncation(run_dir_factory) -> None:
    """§4.2-2: thoughts excerpts are pure truncations — no decoration that would break §4.4-3."""
    round_text = "round text with an anchored state in proofs/stake1/run1/screenshots/click_using_selector_end_1695000000000000002.png and more"
    evidence, _outcome, request = _routed_case(run_dir_factory, rounds=[round_text], screenshots=["click_using_selector_end_1695000000000000002.png"])

    thoughts_items = [item for item in request.seed_evidence if item.kind == "thoughts"]
    assert thoughts_items, "the last rounds always enter the package"
    for item in thoughts_items:
        round_index = int(item.reference.removeprefix("thoughts:"))
        assert item.excerpt == evidence.thoughts[round_index].content_text[:400]
    shot_items = [item for item in request.seed_evidence if item.kind == KIND_SCREENSHOT]
    assert [item.excerpt for item in shot_items] == ["click_using_selector_end_1695000000000000002.png"]
    assert any(name.endswith("[FINAL]") for name in request.screenshot_names)


def test_default_analyzer_prompt_and_lazy_model() -> None:
    """``DefaultAttributorAnalyzer`` builds the configured model lazily and sends system+user messages."""
    captured: dict[str, object] = {}

    class _StubModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return type("Response", (), {"content": '{"category": "inconclusive", "confidence": 0.2, "summary": "x", "suggestion": "", "evidence_refs": []}'})()

    def get_model(role: str, temperature: float):
        captured["role"] = role
        captured["temperature"] = temperature
        return _StubModel()

    request = LlmAnalysisRequest(scenario="s", failure_message="f", final_response=None, seed_evidence=[], screenshot_names=[], feature_excerpt="")
    analyzer = DefaultAttributorAnalyzer(get_model=get_model)
    raw = asyncio.run(analyzer.analyze(request))

    assert captured["role"] == "attributor_analyze"
    assert captured["temperature"] == 0.0
    messages = captured["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "inconclusive" in messages[0]["content"]
    assert "strict JSON" in messages[0]["content"]
    assert json_loads(raw)["category"] == "inconclusive"

    def broken_get_model(role: str, temperature: float):
        raise ValueError("agents_llm_config.json has no attributor_analyze entry")

    from record2gherkin.attributor.evidence import AttributionError

    with pytest.raises(AttributionError):
        asyncio.run(DefaultAttributorAnalyzer(get_model=broken_get_model).analyze(request))


def json_loads(payload: str) -> dict:
    import json

    return json.loads(payload)
