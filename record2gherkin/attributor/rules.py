"""Deterministic rule layer: engine error-string signatures (spec §3).

The rule layer is the primary classifier: Hercules error strings are fixed engine text, so a
signature hit already settles the category. It is model-free and network-free — importing this module
never touches an LLM (spec §8 criterion 2, checked mechanically by a test).

Scan order is fixed for every signature: ``FAIL -> SYSOUT -> THOUGHTS`` (spec §3.1); ``fields`` only
restricts which corpora a signature may match. The first signature that hits wins (table order is the
priority order — S1/S2 must beat S10, whose FAIL text also carries EXPECTED/ACTUAL).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from record2gherkin.attributor.evidence import (
    KIND_FEATURE_FILE,
    KIND_JUNIT_FAILURE,
    KIND_JUNIT_SYSOUT,
    KIND_THOUGHTS,
    REF_FEATURE_PREFIX,
    REF_JUNIT_FAILURE,
    REF_SYSOUT,
    REF_THOUGHTS_PREFIX,
    Category,
    EvidenceEntry,
    Route,
    TestCaseEvidence,
)

# ----------------------------------------------------------------------------------------------
# Signature description language
# ----------------------------------------------------------------------------------------------

SUBSTRING = "substring"
REGEX = "regex"

FRAGMENT = "fragment"
FIELD = "field"

#: Field scan order (spec §3.1) — never reordered, even when the spec table lists a union.
FIELD_ORDER: tuple[str, ...] = ("fail", "sysout", "thoughts")

#: Rule-layer evidence excerpts are whole hit lines truncated to this length (spec §3.2).
RULE_EXCERPT_LIMIT = 500


@dataclass(frozen=True)
class PatternSpec:
    """One case-insensitive pattern (substring or regex)."""

    kind: str
    value: str


def sub(value: str) -> PatternSpec:
    """Case-insensitive substring pattern."""
    return PatternSpec(SUBSTRING, value)


def rx(value: str) -> PatternSpec:
    """Case-insensitive regex pattern."""
    return PatternSpec(REGEX, value)


@dataclass(frozen=True)
class Group:
    """A set of patterns that must all match inside one unit of text.

    ``scope="fragment"``: every pattern must match the *same line* (this is what makes the "same hit
    fragment" clauses of S7/S13 meaningful and what the excerpt line is taken from).
    ``scope="field"``: every pattern must match somewhere in the field text (S3/S10/S11).
    """

    all_of: tuple[PatternSpec, ...]
    scope: str = FRAGMENT


@dataclass(frozen=True)
class Signature:
    """One rule of the spec §3.2 table."""

    id: str
    sid: str
    category: Category | None  # None -> needs_llm (routing signature)
    confidence: float | None
    fields: tuple[str, ...]
    groups: tuple[Group, ...]
    alternative_hypotheses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unit:
    """One scanned corpus unit: FAIL, SYSOUT or a single THOUGHTS round."""

    field: str
    text: str
    round_index: int | None = None


@dataclass
class RuleOutcome:
    """What the rule layer decided (or routed) for one failing case (spec §3.2)."""

    route: Route
    rule_signature: str | None
    confidence: float | None
    evidence: list[EvidenceEntry] = field(default_factory=list)
    alternative_hypotheses: list[str] = field(default_factory=list)
    #: Matching THOUGHTS rounds (first + last of the hit field, spec §3.1); feeds the LLM context
    #: window (§4.2-2) and never reaches the report JSON.
    hit_rounds: list[int] = field(default_factory=list)


# ----------------------------------------------------------------------------------------------
# Signature table (spec §3.2) — strings are copied verbatim from the source anchors
# ----------------------------------------------------------------------------------------------

SIG_PLANNER_MAX_ROUNDS = "planner_max_rounds"
SIG_PLANNER_TIMEOUT = "planner_timeout"
SIG_NAV_MAX_ROUNDS = "nav_max_rounds"
SIG_LLM_CONTEXT_LIMIT = "llm_context_limit"
SIG_NETWORK_DNS = "network_dns"
SIG_BROWSER_DISCONNECTED = "browser_disconnected"
SIG_TOOL_ERROR_NETWORK = "tool_error_network"
SIG_ELEMENT_NOT_FOUND = "element_not_found"
SIG_HTTP_404 = "http_404"
SIG_ASSERTION_MISMATCH = "assertion_mismatch"
SIG_HELPER_UNCERTAIN = "helper_uncertain"
SIG_LLM_CALL_ERROR = "llm_call_error"
SIG_GENERIC_TOOL_ERROR = "generic_tool_error"

#: Networking/transport words used by S7 (spec §3.2).
TOOL_ERROR_NETWORK_RE = rx(r"timed out|timeout|connection|econn|unreachable|refused|reset by peer")

#: ``Element with selector: "X" not found`` / ``since the selector is invalid`` (click_using_selector.py:157-159,306).
ELEMENT_NOT_FOUND_RE = rx(r"(element with selector[^\n]{0,80}not found)|(since the selector is invalid)|((element|node|option|button|field|link|form)[^\n]{0,60}\bnot found\b)")

#: Engine failure marker words (simple_hercules.py:110-124).
HELPER_UNCERTAIN_RE = rx(r"\b(uncertain|incomplete|contradict\w*)\b")

ALL_FIELDS: tuple[str, ...] = FIELD_ORDER
FAIL_SYSOUT: tuple[str, ...] = ("fail", "sysout")

SIGNATURES: tuple[Signature, ...] = (
    Signature(
        id=SIG_PLANNER_MAX_ROUNDS,
        sid="S1",
        category="agent_limit",
        confidence=0.95,
        fields=ALL_FIELDS,  # simple_hercules.py:409 (assert_summary :412-415 repeats it)
        groups=(Group((sub("max planner rounds exceeded"),)),),
    ),
    Signature(
        id=SIG_PLANNER_TIMEOUT,
        sid="S2",
        category="agent_limit",
        confidence=0.90,
        fields=FAIL_SYSOUT,  # simple_hercules.py:320, 357-359
        groups=(
            Group((sub("llm call timed out after"),)),
            Group((sub("planner receives a timely model response"),)),
        ),
    ),
    Signature(
        id=SIG_NAV_MAX_ROUNDS,
        sid="S3",
        category="agent_limit",  # branch (c); (a)/(b) are resolved by the secondary scan
        confidence=0.90,
        fields=ALL_FIELDS,  # simple_hercules.py:1004-1007
        groups=(Group((sub("reached before ##TERMINATE TASK##"), sub("max nav rounds (")), scope=FIELD),),
    ),
    Signature(
        id=SIG_LLM_CONTEXT_LIMIT,
        sid="S4",
        category="agent_limit",
        confidence=0.85,
        fields=ALL_FIELDS,
        groups=(
            Group((rx("context_length_exceeded"),)),
            Group((rx("maximum context length"),)),
            Group((rx("context window.{0,40}exceed"),)),
            Group((rx("prompt is too long"),)),
        ),
    ),
    Signature(
        id=SIG_NETWORK_DNS,
        sid="S5",
        category="environment",
        confidence=0.90,
        fields=ALL_FIELDS,
        groups=(
            Group((rx("net::ERR_[A-Z_]+"),)),
            Group((rx("getaddrinfo"),)),
            Group((rx("Temporary failure in name resolution"),)),
            Group((rx("Name or service not known"),)),
            Group((rx("NS_ERROR_UNKNOWN_HOST"),)),
        ),
    ),
    Signature(
        id=SIG_BROWSER_DISCONNECTED,
        sid="S6",
        category="environment",
        confidence=0.80,
        fields=ALL_FIELDS,
        groups=(
            Group((rx("Target (page, context or browser )?closed"),)),
            Group((rx("browser has been closed"),)),
            Group((rx("Playwright connection (closed|error)"),)),
            Group((rx("Target crashed"),)),
            Group((rx("Session with given id not found"),)),
        ),
    ),
    Signature(
        id=SIG_TOOL_ERROR_NETWORK,
        sid="S7",
        category="environment",
        confidence=0.75,
        fields=ALL_FIELDS,  # simple_hercules.py:682,697 / utils/mcp_help.py:113
        groups=(
            Group((sub("[tool error]"), TOOL_ERROR_NETWORK_RE)),
            Group((sub("[mcp tool error]"), TOOL_ERROR_NETWORK_RE)),
        ),
        alternative_hypotheses=("也可能是被测站点自身故障被记为工具错误",),
    ),
    Signature(
        id=SIG_ELEMENT_NOT_FOUND,
        sid="S8",
        category="test_rot",
        confidence=0.70,
        fields=ALL_FIELDS,
        groups=(Group((ELEMENT_NOT_FOUND_RE,)),),
        alternative_hypotheses=("元素缺失也可能是产品缺陷或 agent 误定位（静默点错）",),
    ),
    Signature(
        id=SIG_HTTP_404,
        sid="S9",
        category="test_rot",
        confidence=0.65,
        fields=ALL_FIELDS,
        groups=(Group((rx(r"""\b404\b[^0-9\n]{0,40}not found|\bstatus["']?[:= ]+ ?404\b"""),)),),
        alternative_hypotheses=("404 也可能因环境路由/代理而非用例过时",),
    ),
    Signature(
        id=SIG_ASSERTION_MISMATCH,
        sid="S10",
        category=None,  # routing: product_bug vs test_rot vs agent mis-click (spec §3.2, plan §8)
        confidence=None,
        fields=("fail",),
        groups=(Group((rx(r"\bexpected\b"), rx(r"\bactual\b")), scope=FIELD),),
        alternative_hypotheses=("产品缺陷（页面行为与预期不符）", "用例过时（预期文案/元素已变）", "agent 误操作（点错目标但步骤记为成功）"),
    ),
    Signature(
        id=SIG_HELPER_UNCERTAIN,
        sid="S11",
        category=None,  # routing
        confidence=None,
        fields=ALL_FIELDS,
        groups=(Group((sub("##TERMINATE TASK##"), HELPER_UNCERTAIN_RE), scope=FIELD),),
    ),
    Signature(
        id=SIG_LLM_CALL_ERROR,
        sid="S12",
        category="agent_limit",
        confidence=0.80,
        fields=ALL_FIELDS,  # simple_hercules.py:917
        groups=(Group((rx(r"\[error\] [a-z_ ]+ llm error:"),)),),
    ),
    Signature(
        id=SIG_GENERIC_TOOL_ERROR,
        sid="S13",
        category=None,  # routing (S7 already claimed the network-flavoured tool errors)
        confidence=None,
        fields=ALL_FIELDS,
        groups=(Group((sub("[tool error]"),)), Group((sub("[mcp tool error]"),))),
    ),
)

SIGNATURES_BY_ID: dict[str, Signature] = {signature.id: signature for signature in SIGNATURES}

#: Fixed, deterministic suggestion templates per category (spec §3.3).
SUGGESTION_TEMPLATES: dict[str, str] = {
    "agent_limit": "引擎轮次/超时上限内未收敛：检查该步骤页面复杂度（弹窗/iframe），考虑提高轮次上限或换强模型档，并重跑确认是否偶发。",
    "test_rot": "页面与录制时可能已不一致：核对失败元素/URL 与录制事件流的原始依据，必要时重录该流程。",
    "environment": "疑似环境问题：确认目标站点可达、代理/DNS 正常、浏览器可用后重跑。",
    "product_bug": "疑似产品缺陷：按证据摘录人工复核页面实际行为与预期差异。",
    "inconclusive": "证据不足以定位：结合 proof 截图人工复查，或补充运行日志后重跑归因。",
}


def signature_sid(signature_id: str | None) -> str | None:
    """``"S1".."S13"`` for a signature id (report display only)."""
    signature = SIGNATURES_BY_ID.get(signature_id or "")
    return signature.sid if signature else None


# ----------------------------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _matches(spec: PatternSpec, text: str) -> bool:
    if spec.kind == SUBSTRING:
        return spec.value.lower() in text.lower()
    return _compiled(spec.value).search(text) is not None


def _first_matching_line(group: Group, text: str) -> str:
    """Evidence line for a field-scope hit: the first line matching any of the group's patterns."""
    for line in text.splitlines():
        if any(_matches(spec, line) for spec in group.all_of):
            return line
    return text


def _match_group(group: Group, text: str) -> str | None:
    """The hit line when the whole group matches ``text``, else ``None``."""
    if group.scope == FRAGMENT:
        for line in text.splitlines():
            if all(_matches(spec, line) for spec in group.all_of):
                return line
        return None
    if all(_matches(spec, text) for spec in group.all_of):
        return _first_matching_line(group, text)
    return None


def _units_of(field_name: str, evidence: TestCaseEvidence) -> list[Unit]:
    if field_name == "fail":
        return [Unit("fail", evidence.failure_message)] if evidence.failure_message else []
    if field_name == "sysout":
        return [Unit("sysout", evidence.system_out)] if evidence.system_out else []
    return [Unit("thoughts", round_.content_text, round_.index) for round_ in evidence.thoughts if round_.content_text]


def _match_units(units: list[Unit], groups: tuple[Group, ...]) -> list[tuple[Unit, str]]:
    hits: list[tuple[Unit, str]] = []
    for unit in units:
        for group in groups:
            line = _match_group(group, unit.text)
            if line is not None:
                hits.append((unit, line))
                break
    return hits


def _scan(signature: Signature, evidence: TestCaseEvidence) -> list[tuple[Unit, str]] | None:
    """Scan FAIL -> SYSOUT -> THOUGHTS and stop at the first field with a hit (spec §3.1)."""
    for field_name in FIELD_ORDER:
        if field_name not in signature.fields:
            continue
        hits = _match_units(_units_of(field_name, evidence), signature.groups)
        if hits:
            return hits
    return None


def _scan_thoughts(evidence: TestCaseEvidence, signature_id: str) -> list[tuple[Unit, str]]:
    """Secondary (S3) scan restricted to THOUGHTS for one signature's patterns."""
    signature = SIGNATURES_BY_ID[signature_id]
    return _match_units(_units_of("thoughts", evidence), signature.groups)


def _evidence_for(unit: Unit, line: str) -> EvidenceEntry:
    if unit.field == "fail":
        kind, reference = KIND_JUNIT_FAILURE, REF_JUNIT_FAILURE
    elif unit.field == "sysout":
        kind, reference = KIND_JUNIT_SYSOUT, REF_SYSOUT
    else:
        kind, reference = KIND_THOUGHTS, f"{REF_THOUGHTS_PREFIX}{unit.round_index}"
    return EvidenceEntry(kind=kind, reference=reference, excerpt=line[:RULE_EXCERPT_LIMIT])


def _rounds_of(hits: list[tuple[Unit, str]]) -> list[int]:
    """First and last matching THOUGHTS rounds of a hit field (spec §3.1)."""
    indices = [unit.round_index for unit, _ in hits if unit.round_index is not None]
    if not indices:
        return []
    return sorted({indices[0], indices[-1]})


def _primary_hits(hits: list[tuple[Unit, str]]) -> list[tuple[Unit, str]]:
    """First + last hit of the field (deduplicated); "首个/首末命中位置" of spec §3.2."""
    if len(hits) == 1:
        return list(hits)
    return [hits[0], hits[-1]]


def _feature_line_evidence(evidence: TestCaseEvidence, fragment: str) -> list[EvidenceEntry]:
    """S8 best-effort: quoted selector found in the hit fragment and present in the feature text (spec §3.2)."""
    if not evidence.feature_text:
        return []
    lines = evidence.feature_text.splitlines()
    for quoted in re.findall(r'"([^"]+)"', fragment):
        for position, line in enumerate(lines, start=1):
            if quoted in line:
                return [EvidenceEntry(kind=KIND_FEATURE_FILE, reference=f"{REF_FEATURE_PREFIX}{position}", excerpt=line[:RULE_EXCERPT_LIMIT])]
    return []


def _nav_max_rounds_outcome(
    evidence: TestCaseEvidence,
    main_entries: list[EvidenceEntry],
    main_rounds: list[int],
) -> RuleOutcome:
    """S3 secondary scan: not found -> test_rot, network/disconnect -> environment, else agent_limit (spec §3.2)."""
    not_found = _scan_thoughts(evidence, SIG_ELEMENT_NOT_FOUND)
    if not_found:
        unit, line = not_found[0]
        return RuleOutcome(
            route="test_rot",
            rule_signature=SIG_NAV_MAX_ROUNDS,
            confidence=0.75,
            evidence=main_entries + [_evidence_for(unit, line)],
            alternative_hypotheses=["轮次耗尽可能因引擎定位能力不足，而非用例过时"],
            hit_rounds=sorted(set(main_rounds) | set(_rounds_of(not_found))),
        )

    transport = _scan_thoughts(evidence, SIG_NETWORK_DNS) or _scan_thoughts(evidence, SIG_BROWSER_DISCONNECTED) or _scan_thoughts(evidence, SIG_TOOL_ERROR_NETWORK)
    if transport:
        unit, line = transport[0]
        return RuleOutcome(
            route="environment",
            rule_signature=SIG_NAV_MAX_ROUNDS,
            confidence=0.75,
            evidence=main_entries + [_evidence_for(unit, line)],
            alternative_hypotheses=[],
            hit_rounds=sorted(set(main_rounds) | set(_rounds_of(transport))),
        )

    return RuleOutcome(
        route="agent_limit",
        rule_signature=SIG_NAV_MAX_ROUNDS,
        confidence=0.90,
        evidence=main_entries,
        alternative_hypotheses=[],
        hit_rounds=list(main_rounds),
    )


def apply_rules(evidence: TestCaseEvidence) -> RuleOutcome:
    """Classify one failing case with the deterministic signature table (spec §3.2).

    Returns a concrete category when a signature hits, otherwise ``route="needs_llm"`` (either a
    routing signature or the fallback long tail) for the LLM layer / degradation path.
    """
    for signature in SIGNATURES:
        hits = _scan(signature, evidence)
        if not hits:
            continue
        primary = _primary_hits(hits)
        entries = [_evidence_for(unit, line) for unit, line in primary]
        rounds = _rounds_of(hits)
        if signature.id == SIG_NAV_MAX_ROUNDS:
            return _nav_max_rounds_outcome(evidence, entries, rounds)
        if signature.id == SIG_ELEMENT_NOT_FOUND:
            entries = entries + _feature_line_evidence(evidence, primary[0][1])
        return RuleOutcome(
            route=signature.category if signature.category is not None else "needs_llm",
            rule_signature=signature.id,
            confidence=signature.confidence,
            evidence=entries,
            alternative_hypotheses=list(signature.alternative_hypotheses),
            hit_rounds=rounds,
        )
    # Fallback: the ambiguous long tail goes to the LLM layer (spec §3.2 last row).
    return RuleOutcome(route="needs_llm", rule_signature=None, confidence=None, evidence=[], alternative_hypotheses=[], hit_rounds=[])
