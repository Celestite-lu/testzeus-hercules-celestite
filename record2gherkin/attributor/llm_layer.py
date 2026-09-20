"""LLM summarisation layer (spec §4): protocol, prompt, output validation, evidence back-check, degradation.

The rule layer routes ambiguous failures here with ``needs_llm``. This layer builds a *numbered*,
byte-deterministic evidence package, asks a weak model for a strict-JSON verdict, and then verifies
every citation against the original artifacts: a reference that cannot be resolved, an excerpt that is
not a substring of its referent, or a conclusion without a surviving text citation is rejected —
after one retry with a machine-readable violation receipt the case degrades to ``inconclusive``.

Nothing here trusts the model (spec §4.4-4/§4.5): the worst case of a hallucinating or unavailable
LLM is an honest ``inconclusive`` that keeps the rule-layer seed evidence.

Spec deviations (documented in ``dev_docs/attributor/test-report.md``):
  * :class:`LlmAnalysisRequest` carries an extra optional ``retry_feedback`` field: the protocol takes
    a request object (spec §4.1), so the §4.4-5 "append the violations as a user message" retry needs a
    carrier for that message.
  * ``LlmAnalyzer.analyze`` is typed ``LlmAnalysisOutput | str | Mapping``: §4.4-1 requires this layer to
    strip fences / extract JSON, so the raw text has to reach it (``DefaultAttributorAnalyzer`` returns
    the raw model text; typed fakes may return an object).
  * The §4.1 "at most 2 attempts" budget lives here (not inside the default analyzer) so the retry and
    its receipt stay observable for any analyzer implementation.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from record2gherkin.attributor.evidence import (
    CATEGORIES,
    KIND_JUNIT_FAILURE,
    KIND_JUNIT_SYSOUT,
    KIND_SCREENSHOT,
    REF_JUNIT_FAILURE,
    REF_SYSOUT_FINAL_RESPONSE,
    TEXT_KINDS,
    AttributionError,
    Category,
    EvidenceEntry,
    TestCaseEvidence,
    final_state_screenshots,
    find_screenshot_anchors,
    normalize_whitespace,
    resolve_reference,
)
from record2gherkin.attributor.rules import SUGGESTION_TEMPLATES, RuleOutcome
from testzeus_hercules.utils.logger import logger

# ----------------------------------------------------------------------------------------------
# Limits and constants (spec §4.1-§4.5)
# ----------------------------------------------------------------------------------------------

#: Weak-model role key resolved through Hercules' ``agents_llm_config.json`` (spec §4.1, plan §5).
DEFAULT_ANALYZE_ROLE = "attributor_analyze"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Total attempts (first call + one retry with a violation receipt) — spec §4.1/§4.4-5.
MAX_ATTEMPTS = 2

FAILURE_MESSAGE_LIMIT = 2000
FINAL_RESPONSE_LIMIT = 2000
FEATURE_EXCERPT_LIMIT = 2000
SUMMARY_LIMIT = 2000
EVIDENCE_EXCERPT_LIMIT = 400
MAX_EVIDENCE_ITEMS = 30
MAX_SCREENSHOT_NAMES = 20
CONTEXT_ROUNDS = 6
CONFIDENCE_MIN = 0.05
CONFIDENCE_MAX = 0.95
DEGRADED_CONFIDENCE = 0.30
MAX_WARNING_REFS = 5

FAILURE_KIND_PARSE = "parse"
FAILURE_KIND_BACKCHECK = "backcheck"

SYSTEM_PROMPT = (
    "You are a failure attribution analyst for automated BDD test runs of the Hercules engine. "
    "You only classify the failure and cite evidence: you never fix bugs and never propose code changes.\n"
    "\n"
    "Categories — choose exactly one:\n"
    "- product_bug: the page behaves differently from what the scenario expects while page and environment are healthy.\n"
    "- test_rot: the scenario is out of date — the element, URL or wording it relies on no longer exists.\n"
    "- environment: the target site, the browser or the network failed (unreachable host, DNS, disconnected browser).\n"
    "- agent_limit: the engine hit a hard limit (max planner/nav rounds, timeout, context window) or the model was not capable enough.\n"
    "- inconclusive: the evidence is not sufficient to choose any of the above.\n"
    "\n"
    "Hard constraints:\n"
    "- evidence_refs may only contain the ref ids listed in the request (E1..En). Never invent file names, round numbers or references.\n"
    "- Prefer inconclusive over guessing. inconclusive is a legal and encouraged answer.\n"
    "- Every non-inconclusive conclusion must cite at least one text evidence item (thoughts / junit / feature_file). "
    "Screenshot file names alone never support a conclusion (you cannot see pixels).\n"
    "- When the failure message compares EXPECTED and ACTUAL, weigh three hypotheses: product bug, outdated scenario, or the "
    "agent clicking the wrong target while the step was recorded as successful.\n"
    "- Answer with strict JSON only: no markdown fences, no explanation.\n"
    '{"category": "test_rot", "confidence": 0.7, "summary": "one line root cause", "suggestion": "one line advice", "evidence_refs": ["E3", "E7"]}'
)

RETRY_INSTRUCTION = "Provide a corrected JSON answer that cites only ref ids listed in this request."


# ----------------------------------------------------------------------------------------------
# Request / response structures (spec §4.1)
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """One numbered evidence item of the LLM package (spec §4.1)."""

    ref_id: str
    kind: str
    reference: str
    excerpt: str


@dataclass
class LlmAnalysisRequest:
    """Everything the analyzer sees about one failing case (spec §4.1)."""

    scenario: str
    failure_message: str
    final_response: str | None
    seed_evidence: list[EvidenceItem]
    screenshot_names: list[str]
    feature_excerpt: str
    #: §4.4-5 retry receipt (machine text appended as an extra user message); ``None`` on the first call.
    retry_feedback: str | None = None


@dataclass
class LlmAnalysisOutput:
    """A schema-valid analyzer answer (spec §4.1); produced only by this layer's validator."""

    category: Category
    confidence: float
    summary: str
    suggestion: str
    evidence_refs: list[str]


class LlmAnalyzer(Protocol):
    """Analyzer hook (spec §4.1); async, so the default implementation can talk to LiteLLM."""

    async def analyze(self, request: LlmAnalysisRequest) -> LlmAnalysisOutput | str | Mapping[str, Any]: ...


@dataclass
class LlmDecision:
    """Outcome of the LLM layer for one case: an accepted verdict or a degradation to ``inconclusive``."""

    category: Category
    confidence: float
    summary: str | None
    suggestion: str
    evidence: list[EvidenceEntry]
    decided_by: str  # "llm" | "degraded"
    warnings: list[str] = field(default_factory=list)
    #: Surviving cited ref ids (E-numbers), kept for the Markdown "LLM 分析" section (spec §6.2).
    evidence_refs: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------------------------
# Evidence package (spec §4.2) — deterministic, numbered E1..En
# ----------------------------------------------------------------------------------------------


def _item(kind: str, reference: str, excerpt: str) -> EvidenceItem:
    return EvidenceItem(ref_id="", kind=kind, reference=reference, excerpt=excerpt[:EVIDENCE_EXCERPT_LIMIT])


def _dedupe_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Drop exact repeats (kind + reference + excerpt) while keeping order (spec §4.2 seed/context join)."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[EvidenceItem] = []
    for item in items:
        key = (item.kind, item.reference, item.excerpt)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _seed_items(evidence: TestCaseEvidence, outcome: RuleOutcome) -> list[EvidenceItem]:
    """Rule-layer hits + FAIL text + final response (spec §4.2-1); never dropped by the package cap."""
    items = [_item(entry.kind, entry.reference, entry.excerpt) for entry in outcome.evidence]
    if evidence.failure_message:
        items.append(_item(KIND_JUNIT_FAILURE, REF_JUNIT_FAILURE, evidence.failure_message))
    if evidence.final_response:
        items.append(_item(KIND_JUNIT_SYSOUT, REF_SYSOUT_FINAL_RESPONSE, evidence.final_response))
    return _dedupe_items(items)


def _context_items(evidence: TestCaseEvidence, outcome: RuleOutcome) -> list[EvidenceItem]:
    """Rule-hit rounds plus the last ``CONTEXT_ROUNDS`` rounds, ascending, ascending unique (spec §4.2-2)."""
    if not evidence.thoughts:
        return []
    indices = set(outcome.hit_rounds)
    indices.update(round_.index for round_ in evidence.thoughts[-CONTEXT_ROUNDS:])
    items: list[EvidenceItem] = []
    for round_ in evidence.thoughts:
        if round_.index in indices and round_.content_text:
            # Pure truncation of content_text: decorating the excerpt would break the §4.4-3 substring check.
            items.append(_item("thoughts", f"thoughts:{round_.index}", round_.content_text))
    return items


def _screenshot_items(evidence: TestCaseEvidence) -> list[EvidenceItem]:
    """Anchored screenshots + the final-state pair, one item per filename, time order (spec §4.2-3)."""
    wanted: set[str] = set()
    for names in find_screenshot_anchors(evidence).values():
        wanted.update(names)
    wanted.update(ref.filename for ref in final_state_screenshots(evidence))
    return [_item(KIND_SCREENSHOT, f"screenshot:{ref.filename}", ref.filename) for ref in evidence.screenshots if ref.filename in wanted]


def _shot_name(item: EvidenceItem) -> str:
    return item.reference[len("screenshot:") :]


def _cap_items(seed: list[EvidenceItem], context: list[EvidenceItem], shots: list[EvidenceItem], protected: set[str]) -> list[EvidenceItem]:
    """Apply the §4.2-4 cap: drop context rounds first, then non-final screenshots; seed is never dropped.

    ``protected`` holds the final-state screenshot filenames; they are the failure-moment context and are
    only nibbled when seed plus the final-state pair alone exceed the limit.
    """
    total = len(seed) + len(context) + len(shots)
    if total > MAX_EVIDENCE_ITEMS:
        logger.debug("attributor: evidence package over %s items (%s), trimming context rounds", MAX_EVIDENCE_ITEMS, total)
    while len(seed) + len(context) + len(shots) > MAX_EVIDENCE_ITEMS and context:
        context.pop(0)
    while len(seed) + len(context) + len(shots) > MAX_EVIDENCE_ITEMS:
        position = next((index for index, item in enumerate(shots) if _shot_name(item) not in protected), None)
        if position is None:
            break
        shots.pop(position)
    return seed + context + shots


def screenshot_names(evidence: TestCaseEvidence) -> list[str]:
    """Time-ordered screenshot file names, ``[FINAL]`` marked, first 20 (spec §4.1)."""
    names: list[str] = []
    for ref in evidence.screenshots[:MAX_SCREENSHOT_NAMES]:
        names.append(f"{ref.filename} [FINAL]" if ref.is_final_state else ref.filename)
    return names


def build_analysis_request(evidence: TestCaseEvidence, outcome: RuleOutcome) -> LlmAnalysisRequest:
    """Build the numbered evidence package for one ``needs_llm`` case (spec §4.2)."""
    items = _cap_items(
        _seed_items(evidence, outcome),
        _context_items(evidence, outcome),
        _screenshot_items(evidence),
        {ref.filename for ref in final_state_screenshots(evidence)},
    )
    numbered = [replace(item, ref_id=f"E{position}") for position, item in enumerate(items, start=1)]
    return LlmAnalysisRequest(
        scenario=evidence.scenario,
        failure_message=(evidence.failure_message or "")[:FAILURE_MESSAGE_LIMIT],
        final_response=(evidence.final_response or "")[:FINAL_RESPONSE_LIMIT] if evidence.final_response else None,
        seed_evidence=numbered,
        screenshot_names=screenshot_names(evidence),
        feature_excerpt=(evidence.feature_text or "")[:FEATURE_EXCERPT_LIMIT],
    )


# ----------------------------------------------------------------------------------------------
# Prompt (spec §4.3)
# ----------------------------------------------------------------------------------------------


def render_user_message(request: LlmAnalysisRequest) -> str:
    """Deterministic user message: context, then the numbered evidence package (spec §4.3)."""
    lines = [
        f"Scenario: {request.scenario}",
        "",
        "Failure message:",
        request.failure_message or "(none)",
        "",
        "Final response:",
        request.final_response or "(none)",
        "",
        "Feature excerpt:",
        request.feature_excerpt or "(none)",
        "",
        "Screenshot filenames (time order; [FINAL] marks the failure-state pair; screenshots and planner rounds are",
        "only approximately aligned by timestamp and literal mentions, not round by round):",
    ]
    lines.extend(f"- {name}" for name in request.screenshot_names or ["(none)"])
    lines.append("")
    lines.append("Evidence package (cite by ref id only):")
    for item in request.seed_evidence:
        lines.append(f"[{item.ref_id}] kind={item.kind} reference={item.reference}")
        lines.append(item.excerpt if item.excerpt else "(empty)")
        lines.append("")
    return "\n".join(lines)


def build_messages(request: LlmAnalysisRequest) -> list[dict[str, str]]:
    """Chat messages for one analysis call (spec §4.3/§4.4-5)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": render_user_message(request)}]
    if request.retry_feedback:
        messages.append({"role": "user", "content": request.retry_feedback})
    return messages


def render_retry_feedback(violations: list[str]) -> str:
    """Machine-readable violation receipt appended as a user message for the retry (spec §4.4-5)."""
    lines = [f"violation: {violation}" for violation in violations]
    lines.append(RETRY_INSTRUCTION)
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# Output validation and evidence back-check (spec §4.4)
# ----------------------------------------------------------------------------------------------


@dataclass
class AttemptResult:
    """Validation result of one analyzer answer (``failure_kind is None`` means accepted)."""

    output: LlmAnalysisOutput | None
    survivors: list[EvidenceItem] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    failure_kind: str | None = None
    dropped_refs: list[str] = field(default_factory=list)
    cited_refs: list[str] = field(default_factory=list)


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def extract_json(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """§4.4-1: strip fences, take the first ``{`` to the last ``}``, ``json.loads``."""
    if isinstance(raw, LlmAnalysisOutput):
        return {"category": raw.category, "confidence": raw.confidence, "summary": raw.summary, "suggestion": raw.suggestion, "evidence_refs": raw.evidence_refs}, None
    if isinstance(raw, Mapping):
        return dict(raw), None
    if not isinstance(raw, str):
        return None, "unparsable_output"
    text = _strip_fence(raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "unparsable_output"
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None, "unparsable_output"
    if not isinstance(data, dict):
        return None, "unparsable_output"
    return data, None


def validate_schema(data: Mapping[str, Any]) -> tuple[LlmAnalysisOutput | None, list[str]]:
    """§4.4-2: five keys, category enum, numeric confidence (clamped), non-empty summary, string refs."""
    violations: list[str] = []
    category = data.get("category")
    if category not in CATEGORIES:
        violations.append("invalid_category")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        violations.append("invalid_confidence")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        violations.append("invalid_summary")
    refs = data.get("evidence_refs")
    if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
        violations.append("invalid_evidence_refs")
    suggestion = data.get("suggestion")
    if suggestion is None:
        suggestion = ""
    elif not isinstance(suggestion, str):
        violations.append("invalid_suggestion")
    if violations:
        return None, violations
    clamped = round(min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, float(confidence))), 2)
    return LlmAnalysisOutput(category=category, confidence=clamped, summary=summary[:SUMMARY_LIMIT], suggestion=suggestion, evidence_refs=list(refs)), []


def backcheck_refs(output: LlmAnalysisOutput, request: LlmAnalysisRequest, evidence: TestCaseEvidence) -> tuple[list[EvidenceItem], list[str], list[str]]:
    """§4.4-3 per-ref check: ``(survivors, violations, dropped refs)``.

    Screenshot items pass on existence (their file was on disk when the package was built); every other
    kind must re-resolve through its §6.3 locator and keep its excerpt a whitespace-normalized substring
    of the referent.
    """
    items_by_ref = {item.ref_id: item for item in request.seed_evidence}
    survivors: list[EvidenceItem] = []
    violations: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for ref in output.evidence_refs:
        if ref in seen:
            continue
        seen.add(ref)
        item = items_by_ref.get(ref)
        if item is None:
            violations.append(f"unknown_ref {ref}")
            dropped.append(ref)
            continue
        referent = resolve_reference(evidence, item.reference)
        if referent is None:
            violations.append(f"unresolvable {ref}")
            dropped.append(ref)
            continue
        if item.kind != KIND_SCREENSHOT and normalize_whitespace(item.excerpt) not in normalize_whitespace(referent):
            violations.append(f"excerpt_mismatch {ref}")
            dropped.append(ref)
            continue
        survivors.append(item)
    return survivors, violations, dropped


def _conclusion_unsupported(category: str, survivors: list[EvidenceItem]) -> bool:
    """§4.4-4: a non-inconclusive verdict needs at least one surviving *text* citation."""
    if category == "inconclusive":
        return False
    return not any(item.kind in TEXT_KINDS for item in survivors)


def validate_attempt(raw: Any, request: LlmAnalysisRequest, evidence: TestCaseEvidence) -> AttemptResult:
    """Run §4.4 steps 1-4 on one analyzer answer."""
    data, violation = extract_json(raw)
    if data is None:
        return AttemptResult(output=None, violations=[violation or "unparsable_output"], failure_kind=FAILURE_KIND_PARSE)
    output, violations = validate_schema(data)
    if output is None:
        return AttemptResult(output=None, violations=violations, failure_kind=FAILURE_KIND_PARSE)
    survivors, backcheck_violations, dropped = backcheck_refs(output, request, evidence)
    cited = [ref for ref in output.evidence_refs]
    if _conclusion_unsupported(output.category, survivors):
        # The verdict cannot stand: per-ref drops plus the refs that could never support a conclusion
        # (screenshots) are reported as dropped (§4.5 warning), because none of them is adopted.
        unsupporting = [item.ref_id for item in survivors if item.kind not in TEXT_KINDS]
        return AttemptResult(
            output=output,
            survivors=survivors,
            violations=backcheck_violations + ["insufficient_evidence"],
            failure_kind=FAILURE_KIND_BACKCHECK,
            dropped_refs=_unique(dropped + unsupporting),
            cited_refs=cited,
        )
    return AttemptResult(output=output, survivors=survivors, violations=backcheck_violations, dropped_refs=_unique(dropped), cited_refs=cited)


def _unique(refs: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    return ordered


# ----------------------------------------------------------------------------------------------
# Degradation (spec §4.5) and the resolution loop (spec §4.4-5)
# ----------------------------------------------------------------------------------------------


def degraded_decision(outcome: RuleOutcome, warning: str, confidence: float = DEGRADED_CONFIDENCE) -> LlmDecision:
    """Inconclusive fallback that keeps the rule-layer seed evidence (spec §4.5)."""
    return LlmDecision(
        category="inconclusive",
        confidence=max(CONFIDENCE_MIN, min(confidence, DEGRADED_CONFIDENCE)),
        summary=None,
        suggestion=SUGGESTION_TEMPLATES["inconclusive"],
        evidence=list(outcome.evidence),
        decided_by="degraded",
        warnings=[warning],
    )


def _accepted_decision(output: LlmAnalysisOutput, survivors: list[EvidenceItem], warnings: list[str]) -> LlmDecision:
    """spec §4.6: adopt the (back-checked) verdict, keep only surviving evidence."""
    suggestion = output.suggestion.strip() or SUGGESTION_TEMPLATES[output.category]
    return LlmDecision(
        category=output.category,
        confidence=output.confidence,
        summary=output.summary,
        suggestion=suggestion,
        evidence=[EvidenceEntry(kind=item.kind, reference=item.reference, excerpt=item.excerpt) for item in survivors],
        decided_by="llm",
        warnings=warnings,
        evidence_refs=[item.ref_id for item in survivors],
    )


def _backcheck_warning(dropped: list[str]) -> str:
    listed = ",".join(dropped[:MAX_WARNING_REFS])
    return f"evidence_backcheck_failed:{listed or '(none)'}"


async def resolve_needs_llm(evidence: TestCaseEvidence, outcome: RuleOutcome, analyzer: LlmAnalyzer) -> LlmDecision:
    """Run the LLM layer for one routed case: at most two attempts, else degrade (spec §4.4-5/§4.5)."""
    request = build_analysis_request(evidence, outcome)
    attempt = 1
    retry_request = request
    last_kind: str | None = None
    last_violations: list[str] = []
    last_dropped: list[str] = []
    last_confidence = DEGRADED_CONFIDENCE
    last_exception: str | None = None

    while attempt <= MAX_ATTEMPTS:
        try:
            raw = await analyzer.analyze(retry_request)
        except AttributionError as exc:
            # Configuration problem: the analyzer cannot run at all -> same handling as analyzer=None.
            logger.warning("attributor: analyzer unavailable (%s)", exc)
            return degraded_decision(outcome, "llm_not_configured")
        except Exception as exc:  # transport/model failure counts as one attempt, no parse retry (spec §4.4-5)
            last_kind = "exception"
            last_exception = type(exc).__name__
            logger.warning("attributor: analyzer call %s/%s failed (%s): %s", attempt, MAX_ATTEMPTS, last_exception, exc)
            attempt += 1
            continue

        result = validate_attempt(raw, request, evidence)
        if result.failure_kind is None:
            # An accepted attempt always carries a validated output (see validate_attempt).
            assert result.output is not None
            warnings = [f"evidence_ref_dropped:{','.join(result.violations)}"] if result.violations else []
            return _accepted_decision(result.output, result.survivors, warnings)

        last_kind = result.failure_kind
        last_violations = result.violations
        last_dropped = result.dropped_refs
        if result.output is not None:
            last_confidence = result.output.confidence
        logger.warning("attributor: analyzer answer rejected on attempt %s/%s (%s)", attempt, MAX_ATTEMPTS, "; ".join(result.violations))
        attempt += 1
        if attempt <= MAX_ATTEMPTS:
            retry_request = replace(request, retry_feedback=render_retry_feedback(last_violations))

    if last_kind == "exception":
        return degraded_decision(outcome, f"llm_error:{last_exception}")
    if last_kind == FAILURE_KIND_BACKCHECK:
        return degraded_decision(outcome, _backcheck_warning(last_dropped), confidence=min(last_confidence, DEGRADED_CONFIDENCE))
    return degraded_decision(outcome, "llm_output_invalid")


# ----------------------------------------------------------------------------------------------
# Default LiteLLM analyzer (spec §4.1)
# ----------------------------------------------------------------------------------------------


def _default_get_model(role: str, temperature: float) -> Any:
    """Build the weak-model role through Hercules' LiteLLM wiring (imported lazily: never at module import)."""
    from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

    model = get_litellm_chat_model(role)
    if hasattr(model, "temperature"):
        model.temperature = temperature
    return model


def _response_text(response: Any) -> str:
    """Best-effort text extraction from a LangChain chat response."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


class DefaultAttributorAnalyzer:
    """LiteLLM-backed analyzer: weak-model role, temperature 0, one 60s call per attempt (spec §4.1).

    The model is built lazily on the first call, so importing/constructing this class never touches the
    network or the LLM configuration. Failures are raised to the caller: retry/degradation decisions
    belong to the layer above (same convention as the distiller polisher).
    """

    def __init__(
        self,
        get_model: Any | None = None,
        *,
        role: str = DEFAULT_ANALYZE_ROLE,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._get_model = get_model or _default_get_model
        self._role = role
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    async def analyze(self, request: LlmAnalysisRequest) -> str:
        """One model call; returns the raw answer text for §4.4-1 extraction.

        A model that cannot be built at all (missing ``attributor_analyze`` key, broken provider config)
        raises :class:`AttributionError`: the caller treats that as "analyzer unavailable" (spec §4.5).
        """
        try:
            model = self._get_model(self._role, self._temperature)
        except AttributionError:
            raise
        except Exception as exc:
            raise AttributionError(f"cannot build LLM model for role {self._role!r}: {exc}") from exc
        response = await asyncio.wait_for(model.ainvoke(build_messages(request)), timeout=self._timeout_seconds)
        return _response_text(response)
