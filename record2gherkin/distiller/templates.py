"""Deterministic rule templates: event stream -> Gherkin skeleton (spec §2).

Pure rules, zero model dependency: this module must not import any LLM/network code (spec §7
acceptance criterion 4). Output is always exactly 1 Feature / 1 Scenario.
"""

from __future__ import annotations

from dataclasses import dataclass

from record2gherkin.distiller.events import (
    MASKED_SENTINEL,
    ParsedInput,
    RecordedEvent,
    fold_literal,
    normalize_whitespace,
)
from record2gherkin.distiller.factcheck import escape_literal

#: Titles are single-lined and truncated to this many characters (spec §2.1).
TITLE_MAX_LENGTH = 120
FEATURE_TITLE_DEFAULT = "Recorded flow"
FEATURE_TITLE_TEMPLATE = "Recorded flow on {origin}"
SCENARIO_TITLE_DEFAULT = "Recorded scenario"

#: Hint fallbacks per event type (spec §2.2); also the exempt template constants of spec §5.2.
HINT_FALLBACK_CLICK = "element"
HINT_FALLBACK_INPUT = "field"
HINT_FALLBACK_SELECT = "dropdown"
HINT_FALLBACK_SUBMIT = "form"
ROLE_FALLBACK = "element"

PLACEHOLDER_KEY_TEMPLATE = "password_{seq}"


@dataclass(frozen=True)
class Skeleton:
    """Rendered skeleton text plus the warnings raised while building it."""

    text: str
    warnings: tuple[str, ...]


def _first_present(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _title(text: str) -> str:
    return normalize_whitespace(text)[:TITLE_MAX_LENGTH]


def _placeholder(key: str) -> str:
    return "{{TEST_DATA:" + key + "}}"


def _render_value(event: RecordedEvent, parsed: ParsedInput, warnings: list[str]) -> str:
    """Insertion-ready value literal for ``input``/``select`` (spec §2.3 + §2.4)."""
    raw = event.value
    if raw is not None and raw.strip() == MASKED_SENTINEL:
        key = PLACEHOLDER_KEY_TEMPLATE.format(seq=event.seq)
        provided = parsed.test_data_values.get(key)
        if provided is not None:
            return escape_literal(fold_literal(provided))
        return _placeholder(key)
    if raw is None:
        warnings.append(f"empty_value:{event.seq}")
        return ""
    folded = fold_literal(raw)
    if not folded:
        warnings.append(f"empty_value:{event.seq}")
    return escape_literal(folded)


def _dedupes_preceding_click(events: tuple[RecordedEvent, ...], index: int) -> bool:
    """True when the ``submit`` at ``index`` is the twin of an immediately preceding ``click`` (spec §2.2).

    One physical submit-button press reaches the recorder as two DOM events, so the stream carries
    ``click(seq N)`` + ``submit(seq N+1)`` for a single user action (recorder spec §4.5). The click step
    keeps the button's name hint and therefore survives; the submit step would be replayed on the
    already-navigated page (the form is gone). A submit without a click predecessor - e.g. an Enter-key
    submission - has no twin and renders normally.
    """
    return index > 0 and events[index].type == "submit" and events[index - 1].type == "click"


def _render_step(event: RecordedEvent, parsed: ParsedInput, first_navigate: bool, warnings: list[str]) -> str | None:
    """One step line for ``event``; ``None`` when the event has to be skipped (spec §2.2 navigate rule)."""
    target = event.target
    if event.type == "navigate":
        url = _first_present(event.url, parsed.origin)
        if not url:
            warnings.append(f"missing_url:{event.seq}")
            return None
        phrase = "I am on the page" if first_navigate else "I navigate to"
        keyword = "Given" if first_navigate else "When"
        return f'{keyword} {phrase} "{escape_literal(fold_literal(url))}"'
    if event.type == "click":
        hint = _first_present(target.name, target.form_label, target.id, target.tag) or HINT_FALLBACK_CLICK
        role = _first_present(target.role, target.tag) or ROLE_FALLBACK
        occurrence = f" (occurrence {target.ordinal})" if target.ordinal is not None and target.ordinal > 1 else ""
        return f'When I click on the "{escape_literal(hint)}" {role}{occurrence}'
    if event.type == "input":
        hint = _first_present(target.form_label, target.name, target.id, target.tag) or HINT_FALLBACK_INPUT
        return f'When I enter "{_render_value(event, parsed, warnings)}" in the "{escape_literal(hint)}" field'
    if event.type == "select":
        hint = _first_present(target.form_label, target.name, target.id) or HINT_FALLBACK_SELECT
        return f'When I select "{_render_value(event, parsed, warnings)}" from the "{escape_literal(hint)}" dropdown'
    if event.type == "submit":
        hint = _first_present(target.name, target.form_label) or HINT_FALLBACK_SUBMIT
        return f'When I submit the "{escape_literal(hint)}" form'
    raise AssertionError(f"unhandled event type {event.type!r}")  # unreachable: parse_input whitelists EVENT_TYPES


def build_skeleton(parsed: ParsedInput) -> Skeleton:
    """Render the deterministic skeleton for a parsed event stream (spec §2.2)."""
    warnings: list[str] = []
    events = parsed.events

    feature_title = _title(FEATURE_TITLE_TEMPLATE.format(origin=parsed.origin)) if parsed.origin else FEATURE_TITLE_DEFAULT
    scenario_title = _title(events[0].page_title) if events and events[0].page_title else SCENARIO_TITLE_DEFAULT

    steps: list[str] = []
    seen_assertions: set[str] = set()
    navigate_seen = False
    for index, event in enumerate(events):
        step: str | None = None
        if not _dedupes_preceding_click(events, index):
            step = _render_step(event, parsed, first_navigate=not navigate_seen, warnings=warnings)
            if step is None:
                # Skipped event (navigate without URL): its assertions are dropped with it (spec §2.2).
                continue
        # A deduped submit contributes no step of its own, but its assertions still render below.
        if step is not None:
            steps.append(step)
        navigate_seen = navigate_seen or event.type == "navigate"
        for text in event.assert_texts:
            if text in seen_assertions:
                continue
            seen_assertions.add(text)
            steps.append(f'Then I should see "{escape_literal(text)}"')

    parts = [f"Feature: {feature_title}", "", f"Scenario: {scenario_title}"]
    if steps:
        parts.append("")
        parts.extend(steps)
    return Skeleton(text="\n".join(parts) + "\n", warnings=tuple(warnings))
