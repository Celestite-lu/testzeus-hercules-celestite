"""Group D (part 1) — report rendering and JSON schema (spec §7 cases 37-38)."""

from __future__ import annotations

import json

from record2gherkin.attributor.api import attribute_run
from record2gherkin.attributor.evidence import CATEGORIES
from record2gherkin.attributor.report import AttributionResult
from record2gherkin.attributor.rules import SUGGESTION_TEMPLATES
from tests.record2gherkin.attributor.conftest import (
    FakeAnalyzer,
    case,
    message,
    output,
    text_ref,
    thoughts_payload,
)

NOT_FOUND_RULE = 'Element with selector: "Go" not found'
SEARCH_FEATURE = 'Feature: Search\n\n  Scenario: search\n    Given the user opens the search page\n    When the user clicks "Go"\n    Then results are shown\n'
JSON_KEYS = {
    "schema_version",
    "scenario",
    "feature",
    "category",
    "confidence",
    "decided_by",
    "rule_signature",
    "llm_summary",
    "suggestion",
    "evidence",
    "warnings",
    "notes",
}


def _rule_result(run_dir_factory) -> AttributionResult:
    run_dir = run_dir_factory(
        cases=[case("search submit", failure="run aborted")],
        thoughts=thoughts_payload(planner=[message("ai", NOT_FOUND_RULE)]),
        feature_text=SEARCH_FEATURE,
    )
    results = attribute_run(str(run_dir))
    assert len(results) == 1
    return results[0]


def test_markdown_required_sections(run_dir_factory) -> None:
    """Case 37: Markdown carries title/conclusion/evidence table, and the warning section when conf<0.8."""
    result = _rule_result(run_dir_factory)
    assert result.confidence < 0.8 and result.alternative_hypotheses
    markdown = result.to_markdown()

    assert markdown.startswith("# 失败归因报告 — search submit")
    assert "- 结论：用例过时（置信度 0.70，判定方式：规则（S8 element_not_found））" in markdown
    assert "## 结论" in markdown
    assert "element_not_found" in markdown and "关键摘录：" in markdown
    assert "## 证据" in markdown
    assert "| # | 类型 | 引用 | 摘录 |" in markdown
    rows = [line for line in markdown.splitlines() if line.startswith("| ") and "|" in line and not line.startswith("| # ") and "---" not in line]
    assert len(rows) == len(result.evidence)
    assert "| 1 | thoughts | thoughts:0 |" in markdown
    assert "| 2 | feature_file | feature:5 |" in markdown
    assert "## 建议" in markdown
    assert SUGGESTION_TEMPLATES["test_rot"] in markdown
    assert "## 警告" in markdown
    assert "替代假设：元素缺失也可能是产品缺陷或 agent 误定位（静默点错）" in markdown
    assert "## 附录" in markdown
    assert "JUnit failure message:" in markdown
    assert "末状态对截图：" in markdown


def test_markdown_llm_section(run_dir_factory) -> None:
    """§6.2: the LLM section appears only for LLM decisions and lists the cited refs."""
    run_dir = run_dir_factory(
        cases=[case("assert", failure="EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item")],
        thoughts=thoughts_payload(planner=[message("ai", "I clicked the wrong button")]),
    )
    analyzer = FakeAnalyzer(builder=lambda request: output(category="product_bug", confidence=0.62, summary="点错了目标控件", suggestion="核对断言前的点击目标", evidence_refs=[text_ref(request)]))
    result = attribute_run(str(run_dir), analyzer=analyzer)[0]

    markdown = result.to_markdown()
    assert result.decided_by == "llm"
    assert "## LLM 分析" in markdown
    assert "点错了目标控件" in markdown
    assert f"- 引用：{', '.join(result.llm_evidence_refs)}" in markdown
    assert result.llm_evidence_refs and result.llm_evidence_refs[0].startswith("E")

    rule_markdown = _rule_result(run_dir_factory).to_markdown()
    assert "## LLM 分析" not in rule_markdown


def test_json_schema_fields(run_dir_factory) -> None:
    """Case 38: JSON keys/enums/confidence bounds, and "no conclusion without evidence"."""
    rule_result = _rule_result(run_dir_factory)

    llm_run = run_dir_factory(
        cases=[case("assert", failure="EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item")],
        thoughts=thoughts_payload(planner=[message("ai", "I clicked the wrong button")]),
    )
    llm_result = attribute_run(str(llm_run), analyzer=FakeAnalyzer(builder=lambda request: output(category="product_bug", confidence=0.62, evidence_refs=[text_ref(request)])))[0]
    degraded_run = run_dir_factory(
        cases=[case("assert", failure="EXPECTED RESULT: order placed\nACTUAL RESULT: cart still shows item")],
        thoughts=thoughts_payload(planner=[message("ai", "I clicked the wrong button")]),
    )
    degraded_result = attribute_run(str(degraded_run), analyzer=None)[0]

    for result in (rule_result, llm_result, degraded_result):
        payload = result.to_dict()
        assert set(payload) == JSON_KEYS
        assert payload["schema_version"] == 1
        assert payload["category"] in CATEGORIES
        assert payload["decided_by"] in {"rule", "llm", "degraded"}
        assert isinstance(payload["confidence"], float) and 0.05 <= payload["confidence"] <= 0.95
        assert isinstance(payload["warnings"], list)
        assert set(payload["notes"]) == {"alternative_hypotheses"}
        for entry in payload["evidence"]:
            assert set(entry) == {"kind", "reference", "excerpt"}
            assert len(entry["excerpt"]) <= 500
        # The core invariant: a non-inconclusive verdict always carries citable evidence.
        if payload["category"] != "inconclusive":
            assert payload["evidence"], f"{payload['decided_by']} decision without evidence"
        if payload["category"] != "inconclusive" and payload["decided_by"] == "rule" and payload["confidence"] < 0.8:
            assert payload["notes"]["alternative_hypotheses"]
        if payload["decided_by"] == "llm":
            assert payload["notes"]["alternative_hypotheses"] == []
            assert payload["llm_summary"]
            assert payload["evidence"] and any(entry["kind"] in {"thoughts", "junit_failure", "junit_sysout", "feature_file"} for entry in payload["evidence"])
        assert json.loads(result.to_json()) == payload

    assert rule_result.rule_signature == "element_not_found"
    assert rule_result.suggestion == SUGGESTION_TEMPLATES["test_rot"]
    assert degraded_result.llm_summary is None
    assert degraded_result.suggestion == SUGGESTION_TEMPLATES["inconclusive"]
    assert degraded_result.alternative_hypotheses  # rule-layer alt survives degradation (spec §4.5)
    assert llm_result.decided_by == "llm" and llm_result.llm_summary


def test_report_is_deterministic(run_dir_factory) -> None:
    """Rendering twice from the same result yields identical text (spec §8 criterion 5, companion)."""
    result = _rule_result(run_dir_factory)
    assert result.to_json() == result.to_json()
    assert result.to_markdown() == result.to_markdown()


def test_appendix_and_table_keep_locators(run_dir_factory) -> None:
    """The evidence table cites §6.3 locators and the appendix carries the raw failure text."""
    result = _rule_result(run_dir_factory)
    markdown = result.to_markdown()
    assert "| 1 | thoughts | thoughts:0 |" in markdown
    assert "| 2 | feature_file | feature:5 |" in markdown
    assert "末状态对截图：（无）" in markdown
    assert "截图清单（时间序，共 0 条" in markdown
    assert "注：截图与 planner 轮次为近似对齐（时间序 + 字面锚），非逐轮映射" in markdown
    assert "run aborted" in markdown, "the appendix shows the raw JUnit failure message"
