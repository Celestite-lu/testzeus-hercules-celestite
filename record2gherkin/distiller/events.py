"""Schema v1 event parsing/normalisation and fact-set construction (spec §1).

This module — like ``templates.py`` and ``factcheck.py`` — must stay free of LLM and network
imports: the skeleton path is deterministic and fully offline (spec §7 acceptance criterion 4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Event types that produce Gherkin steps; anything else is skipped with a warning (spec §1.1).
EVENT_TYPES: tuple[str, ...] = ("navigate", "click", "input", "select", "submit")

#: Exact sentinel the recorder writes for password/sensitive values (spec §1.1, §2.3).
MASKED_SENTINEL = "<masked>"


def normalize_whitespace(value: Any) -> str:
    """Collapse every whitespace run (including newlines/tabs) into one space and strip both ends."""
    return " ".join(str(value).split())


def optional_text(value: Any) -> str | None:
    """Return a whitespace-normalised string, or ``None`` when the value is missing or blank.

    Empty/blank strings are treated as missing so that the template fallback chains kick in
    (spec §2.2 + review note 2), instead of emitting empty literals.
    """
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    text = normalize_whitespace(value)
    return text or None


def fold_literal(value: str) -> str:
    """Apply the spec §2.4 insertion rule: newlines/tabs collapse to one space, ends are stripped."""
    return re.sub(r"[\r\n\t]+", " ", value).strip()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_value(value: Any) -> str | None:
    """Return the raw (un-normalised) event value, or ``None`` when it is missing/blank."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    text = str(value)
    return text if text.strip() else None


@dataclass(frozen=True)
class RecordedTarget:
    """Normalised ``target`` object (spec §1.2 / recorder schema §2.1)."""

    tag: str | None = None
    role: str | None = None
    name: str | None = None
    testid: str | None = None
    id: str | None = None
    ordinal: int | None = None
    form_label: str | None = None

    @staticmethod
    def from_raw(raw: Any) -> "RecordedTarget":
        if not isinstance(raw, Mapping):
            return RecordedTarget()
        return RecordedTarget(
            tag=optional_text(raw.get("tag")),
            role=optional_text(raw.get("role")),
            name=optional_text(raw.get("name")),
            testid=optional_text(raw.get("testid")),
            id=optional_text(raw.get("id")),
            ordinal=_as_int(raw.get("ordinal")),
            form_label=optional_text(raw.get("form_label")),
        )


@dataclass(frozen=True)
class RecordedEvent:
    """A single schema v1 event, normalised and ready for template rendering."""

    seq: int
    type: str
    url: str | None
    page_title: str | None
    value: str | None
    target: RecordedTarget
    assert_texts: tuple[str, ...]


@dataclass(frozen=True)
class Facts:
    """Ground truth for the fact-check layer, built from the recorded input (spec §1.2)."""

    urls: frozenset[str] = field(default_factory=frozenset)
    values: frozenset[str] = field(default_factory=frozenset)
    hints: frozenset[str] = field(default_factory=frozenset)
    assertions: frozenset[str] = field(default_factory=frozenset)
    other: frozenset[str] = field(default_factory=frozenset)
    caller_values: frozenset[str] = field(default_factory=frozenset)

    def literals(self) -> frozenset[str]:
        """Every fact as a single set, used by the grounding check."""
        return frozenset().union(self.urls, self.values, self.hints, self.assertions, self.other, self.caller_values)


@dataclass(frozen=True)
class ParsedInput:
    """Result of normalising an events payload (spec §1.1)."""

    session: Mapping[str, Any]
    origin: str | None
    events: tuple[RecordedEvent, ...]
    facts: Facts
    test_data_values: Mapping[str, str]
    warnings: tuple[str, ...]


def assert_texts_of(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Non-empty, whitespace-normalised ``dom_snapshot.assert_texts`` entries of one raw event."""
    snapshot = raw.get("dom_snapshot")
    if not isinstance(snapshot, Mapping):
        return ()
    texts = snapshot.get("assert_texts")
    if not isinstance(texts, (list, tuple)):
        return ()
    cleaned: list[str] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        normalised = normalize_whitespace(text)
        if normalised:
            cleaned.append(normalised)
    return tuple(cleaned)


def _clean_test_data_values(test_data_values: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only usable ``placeholder key -> real value`` pairs (blank values are treated as absent)."""
    cleaned: dict[str, str] = {}
    if not isinstance(test_data_values, Mapping):
        return cleaned
    for key, value in test_data_values.items():
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (str, int, float)):
            continue
        text = str(value)
        if text.strip():
            cleaned[str(key)] = text
    return cleaned


def build_facts(raw_events: Iterable[Any], origin: str | None = None, caller_values: Iterable[str] = ()) -> Facts:
    """Collect the fact set from the raw events, independent of any generated skeleton text."""
    urls: set[str] = set()
    values: set[str] = set()
    hints: set[str] = set()
    assertions: set[str] = set()
    other: set[str] = set()

    if origin:
        urls.add(origin)
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        url = optional_text(raw.get("url"))
        if url:
            urls.add(url)
        value = _optional_value(raw.get("value"))
        if value is not None and value.strip() != MASKED_SENTINEL:
            values.add(value)
        target = RecordedTarget.from_raw(raw.get("target"))
        for hint in (target.name, target.form_label):
            if hint:
                hints.add(hint)
        if target.ordinal is not None:
            other.add(str(target.ordinal))
        for field_value in (target.tag, target.role, target.id, optional_text(raw.get("page_title"))):
            if field_value:
                other.add(str(field_value))
        assertions.update(assert_texts_of(raw))

    return Facts(
        urls=frozenset(urls),
        values=frozenset(values),
        hints=frozenset(hints),
        assertions=frozenset(assertions),
        other=frozenset(other),
        caller_values=frozenset(caller_values),
    )


def _split_payload(payload: Any, warnings: list[str]) -> tuple[Mapping[str, Any], list[Any]]:
    if isinstance(payload, Mapping):
        session = payload.get("session")
        if session is None:
            session = {}
        elif not isinstance(session, Mapping):
            warnings.append(f"invalid_session_payload:{type(session).__name__}")
            session = {}
        raw_events = payload.get("events")
        if raw_events is None:
            raw_events = []
        elif not isinstance(raw_events, list):
            warnings.append(f"invalid_events_payload:{type(raw_events).__name__}")
            raw_events = []
        return session, list(raw_events)
    if isinstance(payload, list):
        return {}, list(payload)
    warnings.append(f"invalid_input_payload:{type(payload).__name__}")
    return {}, []


def _sort_events(raw_events: list[Any]) -> list[Any]:
    """Sort by ``seq`` ascending; events without a usable ``seq`` keep array order, after the others."""

    def sort_key(item: tuple[int, Any]) -> tuple[int, int]:
        index, raw = item
        seq = _as_int(raw.get("seq")) if isinstance(raw, Mapping) else None
        return (0, seq) if seq is not None else (1, index)

    return [raw for _, raw in sorted(enumerate(raw_events), key=sort_key)]


def parse_input(payload: Any, test_data_values: Mapping[str, Any] | None = None) -> ParsedInput:
    """Normalise a schema v1 payload (``dict`` with ``session``/``events``, or a bare event list).

    Unknown event types are skipped with a warning; missing fields never raise (spec §1.1).
    """
    warnings: list[str] = []
    session, raw_events = _split_payload(payload, warnings)
    origin = optional_text(session.get("origin"))
    caller_values = _clean_test_data_values(test_data_values)
    facts = build_facts(raw_events, origin=origin, caller_values=caller_values.values())

    events: list[RecordedEvent] = []
    for position, raw in enumerate(_sort_events(raw_events)):
        if not isinstance(raw, Mapping):
            warnings.append(f"invalid_event:{type(raw).__name__}")
            continue
        raw_type = raw.get("type")
        if not isinstance(raw_type, str) or raw_type not in EVENT_TYPES:
            warnings.append(f"unknown_event_type:{raw_type if isinstance(raw_type, str) else type(raw_type).__name__}")
            continue
        seq = _as_int(raw.get("seq"))
        events.append(
            RecordedEvent(
                seq=seq if seq is not None else position + 1,
                type=raw_type,
                url=optional_text(raw.get("url")),
                page_title=optional_text(raw.get("page_title")),
                value=_optional_value(raw.get("value")),
                target=RecordedTarget.from_raw(raw.get("target")),
                assert_texts=assert_texts_of(raw),
            )
        )

    if not events:
        warnings.append("empty_events")

    return ParsedInput(
        session=session,
        origin=origin,
        events=tuple(events),
        facts=facts,
        test_data_values=caller_values,
        warnings=tuple(warnings),
    )
