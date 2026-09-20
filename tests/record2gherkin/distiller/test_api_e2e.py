"""Group D — public API and end-to-end (spec §6 cases 31-35)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from record2gherkin.distiller import DistillError, distill_events, distill_file
from record2gherkin.distiller.factcheck import escape_literal
from tests.record2gherkin.distiller.conftest import step_lines
from testzeus_hercules.utils.gherkin_helper import split_feature_file


def _lines_starting_with(text: str, prefix: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip().startswith(prefix)]


def test_end_to_end_sample_flow(login_flow: dict) -> None:
    """Case 31: the login/search fixture distils into one complete, masked, grounded scenario."""
    result = distill_events(login_flow)
    feature = result.feature_text

    assert len(_lines_starting_with(feature, "Feature:")) == 1
    assert len(_lines_starting_with(feature, "Scenario:")) == 1
    assert feature.startswith("Feature: Recorded flow on https_staging_example_com")
    assert "Scenario: Staging Store" in feature
    assert len(step_lines(feature)) == 12

    # every recorded hint reaches the step text (engine-facing positioning hints, plan §2)
    for hint in ("Email", "Password", "Search", "Sort by", "Sign in", "Add to cart"):
        assert f'"{hint}"' in feature
    # ... together with the recorded values and navigations
    for literal in ("qa.user@example.com", "wireless headphones", "Price: Low to High", "https://staging.example.com/login", "https://staging.example.com/search"):
        assert f'"{literal}"' in feature
    # every recorded assertion survives, escaped
    for assertion in ("Welcome back, QA User", "Sign out", "Added to cart", 'Showing 12 results for "wireless headphones"'):
        assert f'Then I should see "{escape_literal(assertion)}"' in feature
    # the masked value never leaks; it becomes a test_data placeholder
    assert "<masked>" not in feature
    assert '"{{TEST_DATA:password_3}}"' in feature
    assert result.used_llm_polish is False
    assert result.warnings == []


def test_end_to_end_form_flow(form_flow: dict) -> None:
    """Spec §2.2: the click+submit dedup, the ordinal suffix and the second navigate render end to end."""
    feature = distill_events(form_flow).feature_text

    # this fixture submits right after a click, i.e. the recorded twin of a submit-button press (P0-1):
    # the click step survives, the submit step is deduped, and the submit's assertion survives below
    assert "When I submit" not in feature
    assert 'When I click on the "Delete" button (occurrence 2)' in feature
    assert 'When I navigate to "https://shop.example.com/confirmation"' in feature
    assert 'When I enter "Deliver to \\"Gate 3\\" before 6pm" in the "Shipping note" field' in feature
    assert 'Then I should see "Order placed successfully"' in feature
    assert "<masked>" not in feature


def test_distill_file_default_output_path(tmp_path: Path, login_flow: dict) -> None:
    """Case 32: ``output_path=None`` writes ``<events stem>.feature`` next to the events file."""
    events_path = tmp_path / "login_search.json"
    events_path.write_text(json.dumps(login_flow, ensure_ascii=False), encoding="utf-8")

    result = distill_file(str(events_path))

    default_output = tmp_path / "login_search.feature"
    assert result.output_path == str(default_output)
    assert default_output.read_text(encoding="utf-8") == result.feature_text

    explicit_output = tmp_path / "nested" / "custom.feature"
    explicit_result = distill_file(str(events_path), output_path=str(explicit_output))
    assert explicit_result.output_path == str(explicit_output)
    assert explicit_output.read_text(encoding="utf-8") == explicit_result.feature_text


def test_distill_file_bad_json_raises(tmp_path: Path) -> None:
    """Case 33: an unreadable events file is a file-level error and raises ``DistillError``."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(DistillError):
        distill_file(str(broken))
    with pytest.raises(DistillError):
        distill_file(str(tmp_path / "missing.json"))


def test_hercules_helper_integration(tmp_path: Path, login_flow: dict) -> None:
    """Case 34: the generated feature is consumable by Hercules' own splitter (1 scenario record)."""
    feature_path = tmp_path / "login_search.feature"
    feature_path.write_text(distill_events(login_flow).feature_text, encoding="utf-8")

    records = asyncio.run(split_feature_file(str(feature_path), str(tmp_path / "split")))

    assert len(records) == 1
    assert records[0]["feature"] == "Recorded flow on https_staging_example_com"
    assert records[0]["scenario"] == "Staging Store"
    assert 'Given I am on the page "https://staging.example.com/login"' in Path(records[0]["output_file"]).read_text(encoding="utf-8")


def test_deterministic_output(tmp_path: Path, login_flow: dict) -> None:
    """Case 35: identical input yields byte-identical output (array order must not matter)."""
    first = distill_events(login_flow)
    second = distill_events(login_flow)
    shuffled = {"session": login_flow["session"], "events": list(reversed(login_flow["events"]))}

    assert first.feature_text == second.feature_text
    assert first.skeleton_text == second.skeleton_text
    assert distill_events(shuffled).feature_text == first.feature_text

    events_path = tmp_path / "login_search.json"
    events_path.write_text(json.dumps(login_flow, ensure_ascii=False), encoding="utf-8")
    first_path = Path(distill_file(str(events_path), output_path=str(tmp_path / "first.feature")).output_path or "")
    second_path = Path(distill_file(str(events_path), output_path=str(tmp_path / "second.feature")).output_path or "")
    assert first_path.read_bytes() == second_path.read_bytes()
