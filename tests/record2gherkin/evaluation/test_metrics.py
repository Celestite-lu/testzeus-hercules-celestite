"""D 组：指标（spec §9 D22-D25，纯函数输入 results.jsonl 行）。"""

from __future__ import annotations

from typing import Any

from record2gherkin.evaluation.metrics import (
    FLOWS,
    MUTATIONS,
    cost_summary,
    failure_rows,
    first_pass,
    headline_survival,
    latest_cell_rows,
    load_rows,
    summarize,
    survival_table,
)


def make_row(flow: str, mutation: str, *, passed: bool, method: str = "generated", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": f"{method}__{flow}__{mutation}__s1",
        "method": method,
        "flow": flow,
        "mutation": mutation,
        "seed": 1,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "duration_s": 12.0,
        "cost_usd": None,
        "total_tokens": None,
        "junit_xml": None,
        "failure_message": None if passed else "assertion failed",
        "started_at": "t0",
        "finished_at": "t1",
        "model": "openai/deepseek-chat",
    }
    row.update(extra)
    return row


def make_generated_grid(passed_by_mutation: dict[str, int]) -> list[dict[str, Any]]:
    """生成每个变异下前 N 条流程通过的行。"""
    rows: list[dict[str, Any]] = []
    for mutation in MUTATIONS:
        passing = passed_by_mutation.get(mutation, 0)
        for index, flow in enumerate(FLOWS):
            rows.append(make_row(flow, mutation, passed=index < passing, cost_usd=0.01 if mutation == "M0" else None, total_tokens=100))
    return rows


def test_d22_first_pass_counts_only_generated_m0() -> None:
    """D22 FirstPass 计算（含某流程失败）与 pilot/它法行不入表。"""
    rows = make_generated_grid({"M0": 5})
    rows.append(make_row("F3", "M0", passed=True, method="baseline"))
    cells, duplicates = latest_cell_rows(rows)
    metric = first_pass(cells)
    assert metric.value == 5 / 6
    assert metric.passed == 5 and metric.total == 6 and metric.missing == ()
    assert duplicates == 0
    # 某流程缺行 → 计 0 且列入 missing
    cells_missing, _ = latest_cell_rows([row for row in rows if row["flow"] != "F2"])
    partial = first_pass(cells_missing)
    assert partial.value == 4 / 6 and partial.missing == ("F2",)
    # 空输入
    empty = first_pass({})
    assert empty.value == 0.0 and empty.total == 6 and len(empty.missing) == 6
    assert first_pass({}, flows=()).value is None


def test_d23_survival_is_grouped_by_method_and_mutation() -> None:
    """D23 Surv 按 (method, mutation) 分组正确；空组/|F|=0 防御。"""
    rows = make_generated_grid({"M0": 6, "M1": 6, "M2": 5, "M3": 1, "M4": 3})
    for flow in FLOWS:
        for mutation in ("M0", "M1", "M2"):
            rows.append(make_row(flow, mutation, passed=mutation != "M2" or flow != "F2", method="baseline"))
    cells, _ = latest_cell_rows(rows)
    table = survival_table(cells)
    assert table["generated"]["M0"].value == 1.0
    assert table["generated"]["M2"].value == 5 / 6
    assert table["generated"]["M3"].value == 1 / 6
    assert table["baseline"]["M2"].value == 5 / 6
    assert table["baseline"]["M4"].value == 0.0  # 无行 → 全 0 且有 missing
    assert table["baseline"]["M4"].missing == FLOWS
    headline = headline_survival(table)
    assert headline["generated"] == (1.0 + 5 / 6 + 1 / 6) / 3
    # baseline 只跑了 M0/M1/M2：主指标里的 M3 无行 → 按 0 计
    assert headline["baseline"] == (1.0 + 5 / 6 + 0.0) / 3
    assert headline_survival({"generated": {}}) == {"generated": None}


def test_d24_cost_aggregation_excludes_none_and_counts_missing() -> None:
    """D24 成本聚合：None 排除出均值、missing 计数正确（不记 0）。"""
    rows = [
        make_row("F1", "M0", passed=True, cost_usd=0.02, total_tokens=1000),
        make_row("F2", "M0", passed=True, cost_usd=None, total_tokens=None),
        make_row("F3", "M0", passed=False, cost_usd=0.04, total_tokens=3000),
        make_row("F1", "M0", passed=True, method="baseline", cost_usd=None, total_tokens=None),
    ]
    summary = cost_summary(rows)
    assert summary.hercules_runs == 3
    assert summary.runs_with_cost == 2
    assert summary.cost_missing_count == 1
    assert summary.avg_cost_usd == 0.03
    assert summary.total_cost_usd == 0.06
    assert summary.total_tokens == 4000
    assert summary.tokens_missing_count == 1
    empty = cost_summary([])
    assert empty.avg_cost_usd is None and empty.total_cost_usd is None and empty.cost_missing_count == 0


def test_d25_timeout_and_no_junit_count_as_zero_in_the_denominator() -> None:
    """D25 timeout/no_junit 计入分母且按 0 计，并出现在失败清单。"""
    rows = [
        make_row("F1", "M3", passed=False, status="timeout", failure_message="timeout after 900s\n..."),
        make_row("F2", "M3", passed=False, status="no_junit", failure_message="junit xml missing at /x"),
        make_row("F3", "M3", passed=False, status="failed", failure_message="Expected text not visible"),
        make_row("F4", "M3", passed=True, status="passed"),
    ]
    cells, _ = latest_cell_rows(rows)
    survival = survival_table(cells)
    assert survival["generated"]["M3"].value == 1 / 6
    assert survival["generated"]["M3"].total == 6
    failures = failure_rows(rows)
    assert {entry["status"] for entry in failures} == {"timeout", "no_junit", "failed"}
    assert all(entry["failure_message"] for entry in failures)
    summary = summarize(rows, exp_id="exp001")
    assert summary.exp_id == "exp001" and summary.rows == 4
    assert summary.survival["generated"]["M3"].value == 1 / 6
    # 同格重复行：最后一行胜出（基础设施重跑）
    retried = rows + [make_row("F1", "M3", passed=True, status="passed", failure_message=None)]
    cells_retried, duplicates = latest_cell_rows(retried)
    assert duplicates == 1
    assert survival_table(cells_retried)["generated"]["M3"].value == 2 / 6


def test_load_rows_skips_garbage_and_tolerates_torn_tail(tmp_path: Any) -> None:
    """补充：results.jsonl 读取容错（空行/断行/非对象行）。"""
    path = tmp_path / "results.jsonl"
    path.write_text(
        '{"run_id": "a", "method": "generated", "passed": true}\n\nnot json at all\n[1, 2, 3]\n{"run_id": "b", "method": "generated", "passed": false}\n{"run_id": "c"',
        encoding="utf-8",
    )
    rows = load_rows(path)
    assert [row["run_id"] for row in rows] == ["a", "b"]
    summary = summarize(rows)
    assert isinstance(summary.as_dict(), dict)
    assert summary.cost.hercules_runs == 2
    assert summary.rows == 2
