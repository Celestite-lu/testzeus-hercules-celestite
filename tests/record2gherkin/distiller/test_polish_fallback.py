"""Group C — polishing and fallback (spec §6 cases 24-30).

Every case injects :class:`FakePolisher`; no real model is ever constructed (spec §4.1).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from record2gherkin.distiller import DefaultPolisher, distill_events
from tests.record2gherkin.distiller.conftest import (
    FakePolisher,
    make_event,
    make_flow,
    skeleton_of,
    step_lines,
)

VALID_REWRITE = "\n".join(
    [
        "Feature: Product search on the staging store",
        "",
        "Scenario: Searching for wireless headphones",
        "",
        'Given I am on the page "https://staging.example.com/search"',
        'When I enter "wireless headphones" in the "Search" field',
        'When I click on the "Search" button',
        'Then I should see "Showing 12 results"',
    ]
)


def search_flow() -> dict[str, Any]:
    return make_flow(
        [
            make_event(1, "navigate", url="https://staging.example.com/search", page_title="Search"),
            make_event(2, "input", target={"tag": "input", "role": "textbox", "name": "Search products", "form_label": "Search"}, value="wireless headphones"),
            make_event(3, "click", target={"name": "Search", "role": "button"}, assert_texts=["Showing 12 results"]),
        ]
    )


def test_polish_used_when_valid() -> None:
    """Case 24: a valid, grounded polished text is adopted."""
    payload = search_flow()
    polisher = FakePolisher(VALID_REWRITE)

    result = distill_events(payload, polisher=polisher)

    assert result.used_llm_polish is True
    assert result.fallback_reason is None
    assert result.feature_text == VALID_REWRITE
    assert result.skeleton_text == skeleton_of(payload)
    assert polisher.calls and polisher.calls[0][0] == result.skeleton_text


def test_fallback_on_ungrounded_literal() -> None:
    """Case 25: any literal the polisher invents drops the whole polished text."""
    payload = search_flow()
    polisher = FakePolisher(transform=lambda skeleton, events: skeleton.replace('"wireless headphones"', '"http://evil.example"'))

    result = distill_events(payload, polisher=polisher)

    assert result.used_llm_polish is False
    assert result.feature_text == result.skeleton_text
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("fact_check_failed")
    assert "http://evil.example" in result.fallback_reason


def test_fallback_on_syntax_invalid() -> None:
    """Case 26: a non-Gherkin response is dropped as ``syntax_invalid``."""
    payload = search_flow()

    result = distill_events(payload, polisher=FakePolisher("not gherkin"))

    assert result.used_llm_polish is False
    assert result.feature_text == result.skeleton_text
    assert result.fallback_reason == "syntax_invalid"


def test_fallback_on_polish_exception() -> None:
    """Case 27: a polisher exception never escapes and degrades to ``polish_error:<ExcType>``."""
    payload = search_flow()

    result = distill_events(payload, polisher=FakePolisher(error=RuntimeError("boom")))

    assert result.used_llm_polish is False
    assert result.feature_text == result.skeleton_text
    assert result.fallback_reason == "polish_error:RuntimeError"


def test_fallback_on_assertion_dropped() -> None:
    """Case 28: dropping a skeleton Then literal is rejected (assertion survival check)."""
    payload = search_flow()
    polisher = FakePolisher(transform=lambda skeleton, events: "\n".join(line for line in skeleton.splitlines() if not line.startswith("Then ")))

    result = distill_events(payload, polisher=polisher)

    assert result.used_llm_polish is False
    assert result.feature_text == result.skeleton_text
    assert result.fallback_reason is not None
    assert result.fallback_reason.startswith("assertion_dropped")
    assert "Showing 12 results" in result.fallback_reason


def test_fallback_on_empty_polish_output() -> None:
    """Spec §3: an empty (or fence-only) polished text is reported as ``polish_empty``."""
    result = distill_events(search_flow(), polisher=FakePolisher("```gherkin\n```"))

    assert result.used_llm_polish is False
    assert result.feature_text == result.skeleton_text
    assert result.fallback_reason == "polish_empty"


def test_no_polisher_no_llm() -> None:
    """Case 29: without a polisher nothing model-related is ever constructed or called."""
    FakePolisher.instances.clear()
    payload = search_flow()

    result = distill_events(payload)

    assert result.used_llm_polish is False
    assert result.fallback_reason is None
    assert result.feature_text == result.skeleton_text
    assert FakePolisher.instances == []


def test_merge_is_polisher_only() -> None:
    """Case 30: the polisher may merge consecutive same-field inputs; the checks must accept it."""
    payload = make_flow(
        [
            make_event(1, "navigate", url="https://staging.example.com/search", page_title="Search"),
            make_event(2, "input", target={"tag": "input", "role": "textbox", "form_label": "Search"}, value="wireless"),
            make_event(3, "input", target={"tag": "input", "role": "textbox", "form_label": "Search"}, value="wireless headphones"),
            make_event(4, "click", target={"name": "Search", "role": "button"}, assert_texts=["Showing 12 results"]),
        ]
    )
    polisher = FakePolisher(transform=lambda skeleton, events: skeleton.replace('When I enter "wireless" in the "Search" field\n', ""))

    result = distill_events(payload, polisher=polisher)

    assert result.used_llm_polish is True
    assert result.fallback_reason is None
    assert [line for line in step_lines(result.feature_text) if line.startswith("When I enter")] == ['When I enter "wireless headphones" in the "Search" field']
    assert len(step_lines(result.feature_text)) == 4


def test_default_polisher_construction_stays_offline() -> None:
    """Spec §4.1: importing/constructing the default polisher must not build a model."""
    factory_calls: list[tuple[str, float]] = []

    def get_model(role: str, temperature: float) -> Any:
        factory_calls.append((role, temperature))
        return _FakeModel([])

    DefaultPolisher(get_model=get_model)

    assert factory_calls == []


class _FakeMessage:
    def __init__(self, content: Any) -> None:
        self.content = content


class _FakeModel:
    """Minimal async chat model double for the DefaultPolisher retry/clean logic."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def ainvoke(self, messages: list[dict[str, str]]) -> _FakeMessage:
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return _FakeMessage(response)


def test_default_polisher_retries_once_and_cleans_fences() -> None:
    """Spec §4.1: one retry on failure, markdown fences stripped, temperature 0 requested."""
    model = _FakeModel([RuntimeError("first attempt fails"), f"```gherkin\n{VALID_REWRITE}\n```"])
    factory_args: list[tuple[str, float]] = []

    def get_model(role: str, temperature: float) -> _FakeModel:
        factory_args.append((role, temperature))
        return model

    polisher = DefaultPolisher(get_model=get_model)
    polished = asyncio.run(polisher.polish(skeleton_of(search_flow()), search_flow()))

    assert polished == VALID_REWRITE
    assert factory_args == [("distiller_polish", 0.0)]
    assert len(model.calls) == 2
    assert "Skeleton feature file" in model.calls[0][1]["content"]


def test_default_polisher_raises_after_attempts_exhausted() -> None:
    """Spec §4.1: the polisher raises on failure; the API layer owns the fallback decision."""
    model = _FakeModel([RuntimeError("first"), RuntimeError("second")])

    polisher = DefaultPolisher(get_model=lambda role, temperature: model)

    with pytest.raises(RuntimeError, match="second"):
        asyncio.run(polisher.polish(skeleton_of(search_flow()), search_flow()))
    assert len(model.calls) == 2
