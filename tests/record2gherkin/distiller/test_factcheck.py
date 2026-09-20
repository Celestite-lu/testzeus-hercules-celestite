"""Group B — fact re-check and structural validation (spec §6 cases 18-23)."""

from __future__ import annotations

import pytest
from record2gherkin.distiller import distill_events
from record2gherkin.distiller.events import Facts, parse_input
from record2gherkin.distiller.factcheck import (
    check_structure,
    escape_literal,
    is_exempt,
    is_grounded,
    unescape_literal,
    ungrounded_literals,
    validate_polished,
    validate_skeleton,
)
from record2gherkin.distiller.templates import build_skeleton
from tests.record2gherkin.distiller.conftest import (
    fixture_names,
    load_fixture,
    make_event,
    make_flow,
    skeleton_of,
)


@pytest.mark.parametrize("fixture_name", fixture_names())
def test_skeleton_always_passes_selfcheck(fixture_name: str) -> None:
    """Case 18 (invariant): the generated skeleton is always structurally valid and grounded."""
    payload = load_fixture(fixture_name)
    parsed = parse_input(payload)
    skeleton = build_skeleton(parsed)

    report = validate_skeleton(skeleton.text, parsed.facts)

    assert report.ok, report
    assert check_structure(skeleton.text) == []
    assert skeleton.text.count("Feature:") == 1
    assert skeleton.text.count("Scenario:") == 1
    # the public API takes the same path and must not raise
    assert distill_events(payload).skeleton_text == skeleton.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        [{"seq": 1, "type": "click", "target": None}],
        [{"type": "input", "value": ""}],
        [{"seq": 1, "type": "select", "value": None, "target": {"tag": "select"}}],
        [{"seq": 4, "type": "input", "value": "<masked>", "target": {"tag": "input"}}],
        ["not-an-event"],
        {"events": "not-a-list"},
    ],
)
def test_any_input_yields_a_valid_skeleton(payload) -> None:
    """Spec §7 criterion 5 / plan §1 corollary 2: legal-looking input always yields a valid feature."""
    parsed = parse_input(payload)
    skeleton = build_skeleton(parsed)

    assert validate_skeleton(skeleton.text, parsed.facts).ok


def test_ungrounded_literal_detected() -> None:
    """Case 19: a literal that was never recorded is reported."""
    events = make_flow([make_event(1, "navigate", url="https://staging.example.com/login", page_title="Login")])
    facts = parse_input(events).facts
    text = "\n".join(
        [
            "Feature: Recorded flow",
            "",
            "Scenario: Login",
            "",
            'Given I am on the page "https://staging.example.com/login"',
            'When I click on the "Buy now" button',
        ]
    )

    assert ungrounded_literals(text, facts) == ["Buy now"]
    report = validate_polished(text, skeleton_of(events), facts)

    assert report.ok is False
    assert report.fallback_reason() == "fact_check_failed:Buy now"


def test_substring_match_passes() -> None:
    """Case 20: a literal that is a contiguous substring of a fact is grounded (tolerates polishing cuts).

    The rule is the normative spec §5.2 one (``norm(L)`` equal to or contained in ``norm(F)``). The
    example of the spec test list (``order placed`` inside ``Your order has been placed
    successfully``) is *not* contiguous; a token-subset test would weaken the gate, so it is only
    asserted here as a documented non-match (see test-report "known issues").
    """
    facts = Facts(assertions=frozenset({"Your order has been placed successfully"}))

    assert is_grounded("order has been placed", facts)
    assert is_grounded("has been placed successfully", facts)
    assert is_grounded("Your order has been placed successfully", facts)
    assert not is_grounded("order placed", facts)
    assert not is_grounded("Your order was cancelled", facts)


def test_whitespace_insensitive_matching() -> None:
    """Case 21: whitespace runs are collapsed on both sides before matching."""
    spaced_fact = Facts(assertions=frozenset({"a  b"}))
    spaced_literal = Facts(assertions=frozenset({"a b"}))

    assert is_grounded("a b", spaced_fact)
    assert is_grounded("a  b", spaced_literal)
    assert is_grounded("a\n\tb", spaced_literal)


def test_quote_escaping_roundtrip() -> None:
    """Case 22: quotes/backslashes are escaped on insertion and unescaped on extraction."""
    value = 'C:\\temp\\file "report".txt'
    events = make_flow(
        [
            make_event(1, "input", target={"tag": "input", "role": "textbox", "form_label": 'Note "x"'}, value=value),
            make_event(2, "click", target={"name": "Save", "role": "button"}, assert_texts=['Saved "quickly"']),
        ]
    )
    text = skeleton_of(events)

    assert f'"{escape_literal(value)}"' in text
    assert unescape_literal(escape_literal(value)) == value
    assert validate_skeleton(text, parse_input(events).facts).ok


def test_placeholder_and_empty_exempt() -> None:
    """Case 23: ``{{TEST_DATA:*}}`` placeholders and empty literals are never violations."""
    facts = Facts(values=frozenset({"recorded value"}))
    text = "\n".join(
        [
            "Feature: Recorded flow",
            "",
            "Scenario: Recorded scenario",
            "",
            'When I enter "{{TEST_DATA:password_1}}" in the "field" field',
            'When I select "" from the "dropdown" dropdown',
            'Then I should see ""',
        ]
    )

    assert is_exempt("{{TEST_DATA:password_1}}")
    assert is_exempt("")
    assert ungrounded_literals(text, facts) == []


def test_template_fallback_words_are_exempt() -> None:
    """Spec §5.2 (review fix 1): the static template fallback words are template constants."""
    facts = Facts()
    text = "\n".join(
        [
            "Feature: Recorded flow",
            "",
            "Scenario: Recorded scenario",
            "",
            'When I click on the "element" element',
            'When I enter "x" in the "field" field',
            'When I select "y" from the "dropdown" dropdown',
            'When I submit the "form" form',
        ]
    )

    assert ungrounded_literals(text, facts) == ["x", "y"]


def test_indented_text_is_structurally_valid() -> None:
    """Spec §5.1 (review fix 2): lines are stripped before anchoring and keyword checks."""
    text = "\n".join(
        [
            "Feature: Recorded flow",
            "",
            "  Scenario: Indented",
            "",
            '    Given I am on the page "https://staging.example.com/"',
            '    When I click on the "Sign in" button',
        ]
    )

    assert check_structure(text) == []
    assert ungrounded_literals(text, Facts(urls=frozenset({"https://staging.example.com/"}))) == ["Sign in"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        'Scenario: no feature\n\nGiven I am on the page "x"',
        'Feature: a\n\nScenario: b\n\nScenario: c\n\nGiven I am on the page "x"',
        'Feature: a\n\nScenario: b\n\nSome free text\n\nGiven I am on the page "x"',
        'Feature: a\n\nScenario: b\n\nI am on the page "x"',
        "# only comments\n\n# nothing else",
    ],
)
def test_structure_violations_are_reported(text: str) -> None:
    """Spec §5.1: every malformed feature text is rejected with structural errors."""
    assert check_structure(text) != []
    assert validate_skeleton(text, Facts(values=frozenset({"x"}))).fallback_reason() == "syntax_invalid"
