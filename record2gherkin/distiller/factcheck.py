"""Literal extraction, structural validation, fact re-check and fallback decision (spec §5).

No LLM or network imports here either (spec §7 acceptance criterion 4): the checker has to stay
usable on the deterministic skeleton path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from record2gherkin.distiller.events import Facts, normalize_whitespace

FEATURE_KEYWORD = "feature:"
SCENARIO_KEYWORD = "scenario:"
STEP_KEYWORDS: tuple[str, ...] = ("given ", "when ", "then ", "and ", "but ")
LINE_KEYWORDS: tuple[str, ...] = (FEATURE_KEYWORD, SCENARIO_KEYWORD) + STEP_KEYWORDS

#: Quoted literal in a step line; ``\\.`` keeps escaped quotes/backslashes inside one match.
LITERAL_PATTERN = re.compile(r'"((?:[^"\\]|\\.)*)"')

#: ``{{TEST_DATA:...}}`` placeholder produced for masked values (spec §2.3).
PLACEHOLDER_PATTERN = re.compile(r"^\{\{TEST_DATA:[^{}]*\}\}$")

#: Static template fallback words (spec §2.2 chains); template constants, not recorded facts (spec §5.2).
TEMPLATE_FALLBACK_LITERALS = frozenset({"element", "field", "dropdown", "form"})

#: Maximum number of literals quoted inside a ``fallback_reason`` (spec §5.3).
MAX_REPORTED_LITERALS = 5


def escape_literal(value: str) -> str:
    """Escape a value for insertion between double quotes (spec §2.4: backslash first, then quote)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def unescape_literal(value: str) -> str:
    """Inverse of :func:`escape_literal` (unknown escapes are kept verbatim)."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in ("\\", '"'):
            out.append(value[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _is_step_line(line: str) -> bool:
    return line.lower().startswith(STEP_KEYWORDS)


def step_lines(feature_text: str) -> list[str]:
    """Step lines of a feature (line-stripped, blank and ``#`` comment lines removed)."""
    lines: list[str] = []
    for raw_line in str(feature_text).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and _is_step_line(line):
            lines.append(line)
    return lines


def extract_literals(feature_text: str) -> list[str]:
    """Every quoted literal of every step line, unescaped, in order (spec §5.2)."""
    literals: list[str] = []
    for line in step_lines(feature_text):
        literals.extend(unescape_literal(match) for match in LITERAL_PATTERN.findall(line))
    return literals


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def is_exempt(literal: str) -> bool:
    """``True`` for literals that never take part in the fact re-check (spec §5.2 exemptions)."""
    text = normalize_whitespace(literal)
    if not text:
        return True
    if PLACEHOLDER_PATTERN.match(text):
        return True
    return text.lower() in TEMPLATE_FALLBACK_LITERALS


def is_grounded(literal: str, facts: Facts) -> bool:
    """``True`` when the literal matches a fact exactly or is a substring of one (spec §5.2)."""
    target = normalize_whitespace(literal)
    if not target:
        return True
    for fact in facts.literals():
        reference = normalize_whitespace(fact)
        if reference and (target == reference or target in reference):
            return True
    return False


def ungrounded_literals(feature_text: str, facts: Facts) -> list[str]:
    """Ungrounded, non-exempt literals of ``feature_text`` (deduplicated, first-seen order)."""
    return _dedupe(literal for literal in extract_literals(feature_text) if not is_exempt(literal) and not is_grounded(literal, facts))


def check_structure(feature_text: str) -> list[str]:
    """Structural validation of spec §5.1; returns a list of human-readable errors (empty == valid)."""
    errors: list[str] = []
    significant = [line.strip() for line in str(feature_text).splitlines()]
    significant = [line for line in significant if line and not line.startswith("#")]
    if not significant:
        return ["empty feature text: expected a 'Feature:' line"]

    lowered = [line.lower() for line in significant]
    if not lowered[0].startswith(FEATURE_KEYWORD):
        errors.append(f"first line must start with 'Feature:' but is {significant[0]!r}")

    feature_count = sum(1 for line in lowered if line.startswith(FEATURE_KEYWORD))
    scenario_count = sum(1 for line in lowered if line.startswith(SCENARIO_KEYWORD))
    if feature_count != 1:
        errors.append(f"expected exactly 1 'Feature:' line, found {feature_count}")
    if scenario_count != 1:
        errors.append(f"expected exactly 1 'Scenario:' line, found {scenario_count}")

    for position, line in enumerate(significant, start=1):
        if not lowered[position - 1].startswith(LINE_KEYWORDS):
            errors.append(f"line {position} must start with one of {LINE_KEYWORDS} but is {line!r}")
    return errors


def assertion_literals(skeleton_text: str) -> list[str]:
    """Quoted literals of the ``Then`` steps of a skeleton (spec §4.3 assertion survival check)."""
    literals: list[str] = []
    for raw_line in str(skeleton_text).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and line.lower().startswith("then "):
            literals.extend(unescape_literal(match) for match in LITERAL_PATTERN.findall(line))
    return literals


def dropped_assertions(skeleton_text: str, polished_text: str) -> list[str]:
    """Skeleton ``Then`` literals that no longer exist in the polished text.

    Survival rule mirrors spec §5.2 with the direction fixed by review note 3: the polished literal
    may shorten the skeleton literal (``polished ⊆ skeleton``) but never lengthen it.
    """
    polished = [normalize_whitespace(literal) for literal in extract_literals(polished_text)]
    polished = [literal for literal in polished if literal]
    dropped: list[str] = []
    for literal in assertion_literals(skeleton_text):
        target = normalize_whitespace(literal)
        if not target:
            continue
        if any(candidate == target or candidate in target for candidate in polished):
            continue
        dropped.append(literal)
    return _dedupe(dropped)


@dataclass(frozen=True)
class ValidationReport:
    """Outcome of validating a feature text; ``ok`` only when every category is empty."""

    structural_errors: tuple[str, ...] = ()
    ungrounded: tuple[str, ...] = ()
    dropped_assertions: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.structural_errors or self.ungrounded or self.dropped_assertions)

    def fallback_reason(self) -> str | None:
        """``fallback_reason`` string per spec §3, or ``None`` when the text passed every check."""
        if self.structural_errors:
            return "syntax_invalid"
        if self.ungrounded:
            return _reason("fact_check_failed", self.ungrounded)
        if self.dropped_assertions:
            return _reason("assertion_dropped", self.dropped_assertions)
        return None


def _reason(prefix: str, literals: Sequence[str]) -> str:
    return f"{prefix}:{', '.join(list(literals)[:MAX_REPORTED_LITERALS])}"


def validate_skeleton(feature_text: str, facts: Facts) -> ValidationReport:
    """Structure + grounding check used for the skeleton self-check (spec §2.5 / §4.3)."""
    errors = check_structure(feature_text)
    if errors:
        return ValidationReport(structural_errors=tuple(errors))
    ungrounded = ungrounded_literals(feature_text, facts)
    return ValidationReport(ungrounded=tuple(ungrounded))


def validate_polished(feature_text: str, skeleton_text: str, facts: Facts) -> ValidationReport:
    """Full polished-output validation (spec §4.3.2-4), short-circuiting on the first failure."""
    errors = check_structure(feature_text)
    if errors:
        return ValidationReport(structural_errors=tuple(errors))
    ungrounded = ungrounded_literals(feature_text, facts)
    if ungrounded:
        return ValidationReport(ungrounded=tuple(ungrounded))
    dropped = dropped_assertions(skeleton_text, feature_text)
    return ValidationReport(dropped_assertions=tuple(dropped))
