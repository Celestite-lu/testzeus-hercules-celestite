"""Group A — evidence loading (spec §7 cases 1-11)."""

from __future__ import annotations

import json

import pytest
from record2gherkin.attributor.evidence import (
    MISSING_FEATURE_FILE,
    MISSING_SCREENSHOTS,
    MISSING_THOUGHTS,
    SCREENSHOT_NAME_RE,
    find_screenshot_anchors,
    load_bundle,
)
from record2gherkin.attributor.rules import apply_rules
from tests.record2gherkin.attributor.conftest import (
    PROP_FEATURE_FILE,
    PROP_PROOFS_SCREENSHOT,
    SAMPLE_RUN_DIR,
    case,
    load_first_case,
    make_run_dir,
    message,
    thoughts_payload,
)


def test_load_synthetic_run_dir(run_dir_factory, sample_run_dir) -> None:
    """Case 1: a complete synthesized run dir loads every field and flattens thoughts in order."""
    bundle = load_bundle(str(sample_run_dir))
    assert bundle.junit_path.endswith("output/junit.xml")
    assert bundle.suite_properties["total_token_used"] == "482113"
    assert len(bundle.cases) == 1

    evidence = bundle.cases[0]
    assert evidence.scenario == "Checkout with a saved card"
    assert evidence.feature_name == "Feature: Checkout with a saved card"
    assert evidence.is_failure
    assert evidence.failure_message and evidence.failure_message.startswith("[ERROR] browser_nav_agent max nav rounds (50)")
    assert evidence.final_response and evidence.final_response.startswith("the order could not be confirmed")
    assert evidence.system_out and "usage_including_cached_inference.total_cost" in evidence.system_out
    assert evidence.terminate == "no"
    assert evidence.properties["target_helper"] == "browser"
    assert evidence.missing == []
    # Flattened round order: planner_agent first, insertion order afterwards, 0-based global index.
    assert [round_.index for round_ in evidence.thoughts] == list(range(7))
    assert evidence.thoughts[0].role == "system"
    assert evidence.thoughts[2].content_json is not None
    assert '"next_step"' in evidence.thoughts[2].content_text
    assert evidence.feature_text and "Scenario: Checkout with a saved card" in evidence.feature_text
    assert [ref.filename for ref in evidence.screenshots] == [
        "click_using_selector_start_1695000000000000001.png",
        "click_using_selector_end_1695000000000000002.png",
        "latest_screenshot.png",
    ]
    assert evidence.thoughts_path and evidence.thoughts_path.endswith("agent_inner_thoughts.json")
    # The JUnit of the fixture stores relative paths, so path resolution went through the run_dir glob.
    assert evidence.feature_path and evidence.feature_path.startswith(str(sample_run_dir))


def test_missing_thoughts_tolerated(run_dir_factory) -> None:
    """Case 2: no inner-thoughts file -> missing marker, rule layer still classifies."""
    run_dir = run_dir_factory(cases=[case("planner hit the round limit", failure="Max planner rounds exceeded.")])
    evidence = load_first_case(run_dir)
    assert evidence.thoughts == []
    assert MISSING_THOUGHTS in evidence.missing
    outcome = apply_rules(evidence)
    assert outcome.route == "agent_limit"
    assert outcome.rule_signature == "planner_max_rounds"


def test_missing_screenshot_dir_tolerated(run_dir_factory) -> None:
    """Case 3: no resolvable screenshot directory -> missing marker plus an empty list."""
    run_dir = run_dir_factory(
        cases=[case("no proofs", failure="Max planner rounds exceeded.")],
        props={PROP_PROOFS_SCREENSHOT: None},
    )
    evidence = load_first_case(run_dir)
    assert evidence.screenshots == []
    assert MISSING_SCREENSHOTS in evidence.missing


def test_thoughts_content_json_and_string(run_dir_factory) -> None:
    """Case 4: dict content is dumped as JSON, multi-line string content is kept verbatim."""
    payload = thoughts_payload(
        planner=[
            message("ai", {"plan": "step 1", "next_step": "click"}),
            message("human", "line one\nline two"),
            message("ai", None),
        ]
    )
    run_dir = run_dir_factory(cases=[case("shapes", failure="no signature here", sysout="plain")], thoughts=payload)
    evidence = load_first_case(run_dir)
    first, second, third = evidence.thoughts
    assert first.content_json == {"plan": "step 1", "next_step": "click"}
    assert first.content_text == json.dumps({"plan": "step 1", "next_step": "click"}, ensure_ascii=False)
    assert second.content_json is None
    assert second.content_text == "line one\nline two"
    assert third.content_text == ""
    assert third.content_json is None


def test_screenshot_parse_tool_phase_ts(run_dir_factory) -> None:
    """Case 5: ``click_using_selector_end_<ns>.png`` parses into tool/phase/ts_ns."""
    run_dir = run_dir_factory(
        cases=[case("shot shape", failure="Max planner rounds exceeded.")],
        screenshots=["click_using_selector_end_1695000000123456789.png"],
    )
    ref = load_first_case(run_dir).screenshots[0]
    assert ref.tool == "click_using_selector"
    assert ref.phase == "end"
    assert ref.ts_ns == 1695000000123456789
    assert ref.is_final_state is True
    assert SCREENSHOT_NAME_RE.match(ref.filename)


def test_screenshot_nonstandard_name(run_dir_factory) -> None:
    """Case 6: a non-standard name becomes phase=other/ts=None and sorts last."""
    run_dir = run_dir_factory(
        cases=[case("shot shape", failure="Max planner rounds exceeded.")],
        screenshots=["latest_screenshot.png", "click_using_selector_start_1695000000000000001.png"],
    )
    refs = load_first_case(run_dir).screenshots
    assert refs[-1].filename == "latest_screenshot.png"
    assert refs[-1].phase == "other"
    assert refs[-1].ts_ns is None
    assert refs[-1].tool == "latest_screenshot"


def test_final_state_pair(run_dir_factory) -> None:
    """Case 7: last ``_end`` plus its closest preceding ``_start`` are the final-state pair."""
    run_dir = run_dir_factory(
        cases=[case("shots", failure="Max planner rounds exceeded.")],
        screenshots=[
            "click_using_selector_start_1695000000000000001.png",
            "fill_using_selector_end_1695000000000000002.png",
            "click_using_selector_end_1695000000000000003.png",
        ],
    )
    refs = load_first_case(run_dir).screenshots
    final = [ref.filename for ref in refs if ref.is_final_state]
    assert final == ["click_using_selector_start_1695000000000000001.png", "click_using_selector_end_1695000000000000003.png"]


def test_screenshot_path_mention_anchor(run_dir_factory) -> None:
    """Case 8: a literal ``*.png`` mention anchors that round; other rounds stay unanchored."""
    payload = thoughts_payload(
        planner=[
            message("ai", "state captured in proofs/stake1/run1/screenshots/click_using_selector_end_1695000000000000002.png"),
            message("human", "browser_nav_agent: clicked the button"),
        ]
    )
    run_dir = run_dir_factory(
        cases=[case("anchor", failure="Max planner rounds exceeded.")],
        thoughts=payload,
        screenshots=["click_using_selector_end_1695000000000000002.png"],
    )
    anchors = find_screenshot_anchors(load_first_case(run_dir))
    assert anchors == {0: ["click_using_selector_end_1695000000000000002.png"]}
    assert 1 not in anchors  # no mention -> no made-up anchor


def test_junit_failure_and_final_response_extraction(run_dir_factory) -> None:
    """Case 9: is_assert failure shape -> failure_message is the assert_summary, final response from system-out."""
    assert_summary = "EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item"
    run_dir = run_dir_factory(
        cases=[case("assert failed", failure=assert_summary, final_response="the order was not placed")],
    )
    evidence = load_first_case(run_dir)
    assert evidence.failure_message == assert_summary
    assert evidence.final_response == "the order was not placed"
    assert evidence.system_out is not None and evidence.system_out.startswith("Final Response: ")
    assert evidence.is_failure


def test_feature_file_property_loaded_and_missing(run_dir_factory) -> None:
    """Case 10: the Feature File property loads the text, or degrades to missing."""
    run_dir = run_dir_factory(cases=[case("feature loaded", failure="Max planner rounds exceeded.")])
    loaded = load_first_case(run_dir)
    assert loaded.feature_text and loaded.feature_text.startswith("Feature: Checkout")
    assert MISSING_FEATURE_FILE not in loaded.missing

    missing_dir = make_run_dir(
        run_dir.parent / "run_missing_feature",
        cases=[case("feature gone", failure="Max planner rounds exceeded.")],
        props={PROP_FEATURE_FILE: str(run_dir.parent / "run_missing_feature" / "gherkin_files" / "ghost.feature")},
    )
    missing = load_first_case(missing_dir)
    assert missing.feature_text is None
    assert missing.feature_path is None
    assert MISSING_FEATURE_FILE in missing.missing


def test_unresolvable_path_suffix_glob(run_dir_factory) -> None:
    """Case 11: a stale property path is recovered by the ``run_dir/**/<filename>`` glob (spec §2.2)."""
    run_dir = run_dir_factory(
        cases=[case("stale paths", failure="Max planner rounds exceeded.")],
        thoughts=thoughts_payload(planner=[message("ai", "planning")]),
        props={
            PROP_FEATURE_FILE: "gherkin_files/ghost/checkout.feature",
            "Planner Thoughts": "log_files/stale/agent_inner_thoughts.json",
        },
    )
    evidence = load_first_case(run_dir)
    assert evidence.feature_path == str(run_dir / "gherkin_files" / "checkout.feature")
    assert evidence.thoughts_path == str(run_dir / "log_files" / "stake1" / "run1" / "agent_inner_thoughts.json")
    assert evidence.thoughts and evidence.thoughts[0].content_text == "planning"
    assert MISSING_FEATURE_FILE not in evidence.missing
    assert MISSING_THOUGHTS not in evidence.missing


def test_junit_missing_raises(run_dir_factory, tmp_path) -> None:
    """Loading rule: only a missing/unparseable JUnit is fatal (spec §2.1)."""
    from record2gherkin.attributor.evidence import AttributionError

    empty = tmp_path / "empty_run"
    empty.mkdir()
    with pytest.raises(AttributionError):
        load_bundle(str(empty))

    broken = run_dir_factory(cases=[case("x", failure="y")], junit_rel="output/junit.xml")
    (broken / "output" / "junit.xml").write_text("<testsuite><testcase>", encoding="utf-8")
    with pytest.raises(AttributionError):
        load_bundle(str(broken))


def test_sample_run_dir_is_self_contained() -> None:
    """The static fixture mirrors the real artifact layout (relative props, 0-byte pngs)."""
    assert (SAMPLE_RUN_DIR / "output" / "junit.xml").is_file()
    assert (SAMPLE_RUN_DIR / "gherkin_files" / "checkout.feature").is_file()
    assert (SAMPLE_RUN_DIR / "log_files" / "stake1" / "run1" / "agent_inner_thoughts.json").is_file()
    shots = sorted((SAMPLE_RUN_DIR / "proofs" / "stake1" / "run1" / "screenshots").glob("*.png"))
    assert len(shots) == 3
    assert all(path.stat().st_size == 0 for path in shots)
