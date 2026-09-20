"""Public attribution API (spec §5).

Both entry points are synchronous: ``attribute_run``/``attribute_case`` drive the async analyzer hook
internally with ``asyncio.run`` (same convention as the distiller API). With ``analyzer=None`` the whole
chain is deterministic — the LLM layer is skipped and every ``needs_llm`` route degrades to
``inconclusive`` with the ``llm_not_configured`` warning.
"""

from __future__ import annotations

import asyncio

from record2gherkin.attributor.evidence import (
    AttributionError,
    TestCaseEvidence,
    load_bundle,
)
from record2gherkin.attributor.llm_layer import (
    DEGRADED_CONFIDENCE,
    LlmAnalyzer,
    LlmDecision,
    degraded_decision,
    resolve_needs_llm,
    screenshot_names,
)
from record2gherkin.attributor.report import AttributionResult
from record2gherkin.attributor.rules import (
    SUGGESTION_TEMPLATES,
    RuleOutcome,
    apply_rules,
)
from testzeus_hercules.utils.logger import logger

__all__ = ["AttributionError", "attribute_case", "attribute_run"]

NOT_A_FAILURE_WARNING = "not_a_failure"


def _base_kwargs(evidence: TestCaseEvidence, warnings: list[str]) -> dict:
    """Fields shared by every result of one case (the §6.2 appendix inputs included)."""
    return {
        "scenario": evidence.scenario,
        "feature": evidence.feature_name,
        "failure_message": evidence.failure_message,
        "screenshot_names": screenshot_names(evidence),
        "warnings": warnings,
    }


def _missing_warnings(evidence: TestCaseEvidence) -> list[str]:
    """``missing_evidence:*`` warnings for every artifact the JUnit referenced but disk did not have (plan §3.3)."""
    return [f"missing_evidence:{marker}" for marker in evidence.missing]


def attribute_case(evidence: TestCaseEvidence, analyzer: LlmAnalyzer | None = None) -> AttributionResult:
    """Attribute a single loaded case (spec §5).

    A testcase without a ``<failure>`` node short-circuits to ``inconclusive`` + ``not_a_failure`` and
    never reaches the rule layer (spec §2.1). Otherwise the deterministic rule layer runs first and only
    a ``needs_llm`` route consults the analyzer.
    """
    warnings = _missing_warnings(evidence)
    if not evidence.is_failure:
        return AttributionResult(
            category="inconclusive",
            confidence=DEGRADED_CONFIDENCE,
            decided_by="rule",
            rule_signature=None,
            llm_summary=None,
            suggestion=SUGGESTION_TEMPLATES["inconclusive"],
            evidence=[],
            alternative_hypotheses=[],
            **_base_kwargs(evidence, warnings + [NOT_A_FAILURE_WARNING]),
        )

    outcome = apply_rules(evidence)
    if outcome.route != "needs_llm":
        return AttributionResult(
            category=outcome.route,
            confidence=outcome.confidence if outcome.confidence is not None else DEGRADED_CONFIDENCE,
            decided_by="rule",
            rule_signature=outcome.rule_signature,
            llm_summary=None,
            suggestion=SUGGESTION_TEMPLATES[outcome.route],
            evidence=outcome.evidence,
            alternative_hypotheses=outcome.alternative_hypotheses,
            **_base_kwargs(evidence, warnings),
        )

    decision = _resolve_needs_llm(evidence, analyzer, outcome)
    if decision.decided_by == "degraded":
        logger.debug("attributor: case %r degraded to inconclusive (%s)", evidence.scenario, decision.warnings)
    return AttributionResult(
        category=decision.category,
        confidence=decision.confidence,
        decided_by=decision.decided_by,
        rule_signature=outcome.rule_signature,
        llm_summary=decision.summary,
        suggestion=decision.suggestion,
        evidence=decision.evidence,
        alternative_hypotheses=outcome.alternative_hypotheses if decision.decided_by == "degraded" else [],
        llm_evidence_refs=decision.evidence_refs,
        **_base_kwargs(evidence, warnings + decision.warnings),
    )


def _resolve_needs_llm(evidence: TestCaseEvidence, analyzer: LlmAnalyzer | None, outcome: RuleOutcome) -> LlmDecision:
    """Run the LLM layer, or degrade straight away when no analyzer was supplied (spec §3.4/§4.5)."""
    if analyzer is None:
        return degraded_decision(outcome, "llm_not_configured")
    return asyncio.run(resolve_needs_llm(evidence, outcome, analyzer))


def attribute_run(
    run_dir: str,
    junit_path: str | None = None,
    analyzer: LlmAnalyzer | None = None,
) -> list[AttributionResult]:
    """Attribute every failing testcase of one run directory (spec §5).

    ``junit_path=None`` picks the newest ``run_dir/**/*.xml``; cases without a ``<failure>`` node are
    filtered out, so a fully green run yields an empty list.
    """
    bundle = load_bundle(run_dir, junit_path)
    results = [attribute_case(case, analyzer=analyzer) for case in bundle.cases if case.is_failure]
    logger.info("attributor: %s of %s testcase(s) failed and were attributed", len(results), len(bundle.cases))
    return results
