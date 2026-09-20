"""Report layer (spec §6): the structured result plus its JSON and Markdown renderings.

This layer never decides anything — it only assembles the fields produced by the rule layer, the LLM
layer or the degradation path. ``to_json()`` is byte-deterministic for deterministic input
(spec §8 criterion 5), which is what the "same run, no analyzer, twice" test compares.

The three extra fields (``failure_message``, ``screenshot_names``, ``llm_evidence_refs``) exist only to
render the §6.2 Markdown appendix / LLM section; they are deliberately *not* part of the §6.1 JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from record2gherkin.attributor.evidence import (
    KIND_JUNIT_FAILURE,
    Category,
    EvidenceEntry,
)
from record2gherkin.attributor.rules import signature_sid

SCHEMA_VERSION = 1

#: Chinese labels used by the Markdown report body (spec §6.2).
CATEGORY_LABELS: dict[str, str] = {
    "product_bug": "产品缺陷",
    "test_rot": "用例过时",
    "environment": "环境问题",
    "agent_limit": "引擎限制",
    "inconclusive": "证据不足",
}

DECIDED_BY_LABELS: dict[str, str] = {"rule": "规则", "llm": "LLM", "degraded": "降级"}

APPENDIX_FAILURE_LIMIT = 2000
APPENDIX_SCREENSHOT_LIMIT = 20

ALIGNMENT_NOTE = "截图与 planner 轮次为近似对齐（时间序 + 字面锚），非逐轮映射"


@dataclass
class AttributionResult:
    """One failing case, attributed (spec §6.1)."""

    scenario: str
    feature: str
    category: Category
    confidence: float
    decided_by: str  # "rule" | "llm" | "degraded"
    rule_signature: str | None
    llm_summary: str | None
    suggestion: str
    evidence: list[EvidenceEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    alternative_hypotheses: list[str] = field(default_factory=list)
    # Markdown-only appendix inputs (spec §6.2), never serialized (spec §6.1 schema is exact):
    failure_message: str | None = None
    screenshot_names: list[str] = field(default_factory=list)
    llm_evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Structured form with the spec §6.1 key order (``schema_version`` first)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "scenario": self.scenario,
            "feature": self.feature,
            "category": self.category,
            "confidence": self.confidence,
            "decided_by": self.decided_by,
            "rule_signature": self.rule_signature,
            "llm_summary": self.llm_summary,
            "suggestion": self.suggestion,
            "evidence": [{"kind": entry.kind, "reference": entry.reference, "excerpt": entry.excerpt} for entry in self.evidence],
            "warnings": list(self.warnings),
            "notes": {"alternative_hypotheses": list(self.alternative_hypotheses)},
        }

    def to_json(self) -> str:
        """JSON rendering; deterministic for deterministic input (spec §8 criterion 5)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """Chinese Markdown report with the sections of spec §6.2 (excerpts stay verbatim)."""
        lines = [
            f"# 失败归因报告 — {self.scenario}",
            f"- 结论：{CATEGORY_LABELS.get(self.category, self.category)}（置信度 {self.confidence:.2f}，判定方式：{_decided_by_text(self)}）",
            "",
            "## 结论",
            self._conclusion_line(),
            "",
            "## 证据",
            "| # | 类型 | 引用 | 摘录 |",
            "| --- | --- | --- | --- |",
        ]
        if self.evidence:
            lines.extend(f"| {position} | {entry.kind} | {_cell(entry.reference)} | {_cell(entry.excerpt)} |" for position, entry in enumerate(self.evidence, start=1))
        else:
            lines.append("| - | - | - | （无证据） |")

        if self.decided_by == "llm":
            lines.extend(["", "## LLM 分析", f"- 摘要：{self.llm_summary or ''}", f"- 引用：{', '.join(self.llm_evidence_refs) or '（无）'}"])
        lines.extend(["", "## 建议", self.suggestion])
        if self.warnings or self.alternative_hypotheses:
            lines.extend(["", "## 警告"])
            lines.extend(f"- {warning}" for warning in self.warnings)
            lines.extend(f"- 替代假设：{hypothesis}" for hypothesis in self.alternative_hypotheses)
        lines.extend(["", "## 附录"])
        lines.extend(self._appendix())
        return "\n".join(lines) + "\n"

    def _conclusion_line(self) -> str:
        if self.decided_by == "llm":
            return self.llm_summary or ""
        label = CATEGORY_LABELS.get(self.category, self.category)
        if self.rule_signature:
            sid = signature_sid(self.rule_signature)
            named = f"{self.rule_signature}（{sid}）" if sid else self.rule_signature
            excerpt = _first_line(self.evidence[0].excerpt) if self.evidence else ""
            return f"规则签名 {named} 命中：归类为{label}。关键摘录：{excerpt}"
        return f"未命中任何签名（路由至 LLM 层/证据不足）：归类为{label}。"

    def _appendix(self) -> list[str]:
        failure = self.failure_message
        if failure is None:
            failure = next((entry.excerpt for entry in self.evidence if entry.kind == KIND_JUNIT_FAILURE), "")
        final_pair = [name for name in self.screenshot_names if name.endswith("[FINAL]")]
        lines = ["JUnit failure message:", "```", _truncate(failure or "(未采集)", APPENDIX_FAILURE_LIMIT), "```"]
        lines.append(f"末状态对截图：{'、'.join(name.removesuffix(' [FINAL]') for name in final_pair) or '（无）'}")
        lines.append(f"截图清单（时间序，共 {len(self.screenshot_names)} 条，最多列 {APPENDIX_SCREENSHOT_LIMIT} 条）：")
        lines.extend(f"- {name}" for name in self.screenshot_names[:APPENDIX_SCREENSHOT_LIMIT])
        if not self.screenshot_names:
            lines.append("- （无）")
        lines.append(f"注：{ALIGNMENT_NOTE}")
        return lines


def _decided_by_text(result: AttributionResult) -> str:
    label = DECIDED_BY_LABELS.get(result.decided_by, result.decided_by)
    if result.decided_by == "rule" and result.rule_signature:
        sid = signature_sid(result.rule_signature)
        return f"{label}（{sid} {result.rule_signature}）" if sid else f"{label}（{result.rule_signature}）"
    return label


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")
