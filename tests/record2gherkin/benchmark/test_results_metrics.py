"""E 组：结果组装与指标（spec §9 E17-E20，规则见 §7.2/§8）。

结果行组装是纯函数（``build_result_row``），指标是纯聚合：两类输入都用手写 fixture，无 LLM、无浏览器。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import metrics as metrics_module
from record2gherkin.benchmark.orchestrator import (
    STATUS_NO_GOAL,
    STATUS_NO_JUNIT,
    STATUS_NO_REWARD,
    STATUS_OFFICIAL_FAILED,
    STATUS_OFFICIAL_PASSED,
    STATUS_TIMEOUT,
    build_result_row,
)

TASK: dict[str, Any] = {
    "task_id": "miniwob.click-test",
    "subdomain": "click-test",
    "family": "click-test",
    "visual": False,
    "desc": "Click the button.",
    "html": "miniwob_html/miniwob/click-test.html",
}
BASE_ROW: dict[str, Any] = {
    "task": TASK,
    "seed": 42,
    "goal": "Click the button.",
    "episode_ms": 240000,
    "runner_status": "passed",
    "reward": {"path": "/miniwob/click-test.html", "seed": "42", "reward": 1, "raw": 1, "done": True, "reason": ""},
    "junit_passed": True,
    "junit_terminate": "##TERMINATE TASK##",
    "junit_xml": "/tmp/run/output/x_result.xml",
    "failure_message": None,
    "duration_s": 12.5,
    "total_tokens": 47000,
    "cost_usd": 0.05,
    "started_at": "2026-09-21T00:00:00+00:00",
    "finished_at": "2026-09-21T00:01:00+00:00",
    "model": "deepseek-v4-pro",
}


def row(**overrides: Any) -> dict[str, Any]:
    return build_result_row(**{**BASE_ROW, **overrides})


# ---------------------------------------------------------------------------------------------
# E17-E19 状态组装 (spec §7.2)
# ---------------------------------------------------------------------------------------------


def test_e17_page_reward_outranks_infra_but_infra_sticks_without_one() -> None:
    """E17（r2 C1a）：raw>0 压倒 infra 状态（救援格）；raw<=0/缺失时 infra 状态保留（仍可重试）。"""
    rescued_timeout = row(runner_status="timeout", junit_passed=None, junit_terminate=None, junit_xml=None, duration_s=600.0, total_tokens=None, cost_usd=None)
    assert rescued_timeout["status"] == STATUS_OFFICIAL_PASSED  # 引擎超时但页面已过 → 救援
    assert rescued_timeout["official_passed"] is True
    assert rescued_timeout["runner_status"] == STATUS_TIMEOUT  # 披露键：r1 口径下该行是 timeout
    assert rescued_timeout["reward_raw"] == 1.0
    assert rescued_timeout["done"] is True
    # disagreement 语义不变（§1.1）：official 行都计算；junit 缺失时上游表达式落 False（r1 已有语义）
    assert rescued_timeout["disagreement"] is False
    assert rescued_timeout["total_tokens"] is None

    # 页面判负（含 timed out 的 raw=-1）不可能被误判通过：infra 状态保留
    timed_out_negative = row(runner_status="timeout", reward={"raw": -1, "done": True, "reason": "timed out"}, junit_passed=None, duration_s=600.0, total_tokens=None, cost_usd=None)
    assert timed_out_negative["status"] == STATUS_TIMEOUT and timed_out_negative["official_passed"] is False

    timed_out_without_reward = row(runner_status="timeout", reward=None, junit_passed=None, duration_s=600.0)
    assert timed_out_without_reward["status"] == STATUS_TIMEOUT
    assert timed_out_without_reward["reward_raw"] is None and timed_out_without_reward["done"] is None

    rescued_no_junit = row(runner_status="no_junit", junit_passed=None, junit_terminate=None, junit_xml=None)
    assert rescued_no_junit["status"] == STATUS_OFFICIAL_PASSED
    assert rescued_no_junit["runner_status"] == STATUS_NO_JUNIT

    # 无页面正奖励时顺序不变：超时优先于 no_junit / no_reward（§7.2.1 → §7.2.2 → §7.2.3）
    both = row(runner_status="timeout", reward=None, junit_passed=None)
    assert both["status"] == STATUS_TIMEOUT
    assert both["runner_status"] == STATUS_TIMEOUT


def test_e18_missing_reward_and_missing_goal() -> None:
    """E18：reward 缺失 → no_reward 且 disagreement=null；goal 失败 → no_goal 不执行。"""
    no_reward = row(runner_status="failed", reward=None, junit_passed=False)
    assert no_reward["status"] == STATUS_NO_REWARD
    assert no_reward["official_passed"] is False
    assert no_reward["disagreement"] is None
    assert no_reward["reward_raw"] is None and no_reward["done"] is None and no_reward["reward_reason"] is None
    assert no_reward["junit_passed"] is False  # JUnit 侧结论保留，但官方计失败

    no_goal = row(
        runner_status=None,
        goal=None,
        reward=None,
        junit_passed=None,
        junit_terminate=None,
        junit_xml=None,
        failure_message="no_goal: empty utterance",
        duration_s=None,
        total_tokens=None,
        cost_usd=None,
    )
    assert no_goal["status"] == STATUS_NO_GOAL
    assert no_goal["official_passed"] is False
    assert no_goal["goal"] is None
    assert no_goal["junit_xml"] is None and no_goal["reward_raw"] is None
    assert no_goal["disagreement"] is None


def test_e19_official_verdict_and_disagreement_both_directions() -> None:
    """E19：官方只看 ``reward_raw > 0``；disagreement 双向记录，一致为 False。"""
    junit_ok_reward_zero = row(reward={"raw": 0, "done": True, "reason": ""}, junit_passed=True)
    assert junit_ok_reward_zero["status"] == STATUS_OFFICIAL_FAILED
    assert junit_ok_reward_zero["official_passed"] is False
    assert junit_ok_reward_zero["disagreement"] is True

    reward_ok_junit_failed = row(reward={"raw": 1, "done": True, "reason": ""}, junit_passed=False)
    assert reward_ok_junit_failed["status"] == STATUS_OFFICIAL_PASSED
    assert reward_ok_junit_failed["official_passed"] is True
    assert reward_ok_junit_failed["disagreement"] is True

    agreeing = row(reward={"raw": 1, "done": True, "reason": ""}, junit_passed=True)
    assert agreeing["status"] == STATUS_OFFICIAL_PASSED and agreeing["disagreement"] is False

    agreeing_failure = row(reward={"raw": 0, "done": True, "reason": ""}, junit_passed=False)
    assert agreeing_failure["status"] == STATUS_OFFICIAL_FAILED and agreeing_failure["disagreement"] is False

    # 页面自身超时：raw=-1 → 官方失败，原因照录（§7.2.1）
    page_timeout = row(reward={"raw": -1, "done": True, "reason": "timed out"}, junit_passed=False)
    assert page_timeout["status"] == STATUS_OFFICIAL_FAILED
    assert page_timeout["reward_raw"] == -1
    assert page_timeout["reward_reason"] == "timed out"

    # 行结构与 §7.1 的键集合一致
    assert set(row()) == set(metrics_module.ROW_KEYS)


def test_t2_row_schema_carries_the_three_r2_keys_and_metrics_ignore_them() -> None:
    """T2：行键 == 更新后的 ROW_KEYS（含 runner_status/attempt/infra_circuit_break）；三键零进分母。"""
    from record2gherkin.benchmark import orchestrator as orchestrator_module

    fresh = row()
    assert fresh["runner_status"] == STATUS_OFFICIAL_PASSED  # runner passed + raw>0 → r1 口径同样通过
    assert fresh["attempt"] == 1  # 无重试恒 1
    assert fresh["infra_circuit_break"] is False

    rescue = row(runner_status="timeout", junit_passed=None, junit_xml=None, junit_terminate=None, duration_s=600.0, total_tokens=None, cost_usd=None)
    assert set(rescue) == set(metrics_module.ROW_KEYS)
    assert set(metrics_module.ROW_KEYS) >= {"runner_status", "attempt", "infra_circuit_break"}

    rows = [
        {**rescue, "infra_circuit_break": True, "attempt": 2},
        _metric_row(TASKS[1], status=STATUS_OFFICIAL_FAILED, official_passed=False, reward_raw=0, junit_passed=False, disagreement=False),
    ]
    for extra in ({"runner_status": "bogus"}, {"attempt": 99}, {"infra_circuit_break": True}):
        mutated = dict(rows[0])
        mutated.update(extra)
        baseline = metrics_module.summarize([mutated, rows[1]], tasks=TASKS).overall
        reference = metrics_module.summarize(rows, tasks=TASKS).overall
        assert (baseline.passed, baseline.total) == (reference.passed, reference.total)  # 不进分母/判定
    # no_goal 行的 runner_status 落 None（attempt 键仍在）
    no_goal = orchestrator_module.build_result_row(
        task=TASK,
        seed=42,
        goal=None,
        episode_ms=240000,
        runner_status=None,
        reward=None,
        junit_passed=None,
        junit_terminate=None,
        junit_xml=None,
        failure_message="no_goal: x",
        duration_s=None,
        total_tokens=None,
        cost_usd=None,
        started_at="2026-09-22T00:00:00+00:00",
        finished_at="2026-09-22T00:00:01+00:00",
    )
    assert no_goal["status"] == STATUS_NO_GOAL and no_goal["runner_status"] is None
    assert no_goal["attempt"] == 1 and no_goal["infra_circuit_break"] is False


# ---------------------------------------------------------------------------------------------
# E20 指标 (spec §8)
# ---------------------------------------------------------------------------------------------

TASKS: list[dict[str, Any]] = [
    {"task_id": "miniwob.click-test", "subdomain": "click-test", "family": "click-test", "visual": False},
    {"task_id": "miniwob.click-test-2", "subdomain": "click-test-2", "family": "click-test", "visual": False},
    {"task_id": "miniwob.enter-text", "subdomain": "enter-text", "family": "enter-text", "visual": False},
    {"task_id": "miniwob.login-user", "subdomain": "login-user", "family": "login-user", "visual": False},
    {"task_id": "miniwob.visual-addition", "subdomain": "visual-addition", "family": "visual-addition", "visual": True},
]


def _metric_row(
    task: dict[str, Any],
    *,
    seed: int = 1,
    status: str = STATUS_OFFICIAL_PASSED,
    official_passed: bool = True,
    reward_raw: float | None = 1.0,
    junit_passed: bool | None = True,
    disagreement: bool | None = False,
    total_tokens: int | None = 1000,
    duration_s: float | None = 10.0,
    cost_usd: float | None = 0.01,
    failure_message: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": f"miniwob__{task['subdomain']}__s{seed}",
        "task_id": task["task_id"],
        "subdomain": task["subdomain"],
        "family": task["family"],
        "visual": task["visual"],
        "seed": seed,
        "goal": "do it",
        "episode_max_time_ms": 240000,
        "status": status,
        "official_passed": official_passed,
        "reward_raw": reward_raw,
        "done": True,
        "reward_reason": "",
        "junit_passed": junit_passed,
        "junit_terminate": None,
        "disagreement": disagreement,
        "duration_s": duration_s,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "junit_xml": None,
        "failure_message": failure_message,
        "started_at": "2026-09-21T00:00:00+00:00",
        "finished_at": "2026-09-21T00:01:00+00:00",
        "model": "deepseek-v4-pro",
    }


def test_e20_overall_denominator_covers_every_failure_family_and_missing_cells() -> None:
    """E20：Overall 分母含全部任务；timeout/no_junit/no_reward 计 0；缺行计 0 并入 missing。"""
    rows = [
        _metric_row(TASKS[0]),  # passed
        _metric_row(TASKS[1], status=STATUS_OFFICIAL_FAILED, official_passed=False, reward_raw=0, junit_passed=False, disagreement=False),
        _metric_row(TASKS[2], status=STATUS_TIMEOUT, official_passed=False, reward_raw=None, junit_passed=None, disagreement=None, total_tokens=None, cost_usd=None),
        _metric_row(TASKS[3], status=STATUS_NO_REWARD, official_passed=False, reward_raw=None, junit_passed=True, disagreement=None),
        # TASKS[4] (visual) 无行 → 计 0 并入 missing
    ]
    summary = metrics_module.summarize(rows, tasks=TASKS, exp_id="e20")
    assert summary.overall.total == 5
    assert summary.overall.passed == 1
    assert summary.overall.value == pytest.approx(0.2)
    assert summary.overall.missing == ("miniwob.visual-addition",)
    assert summary.rows == 4 and summary.cells == 4 and summary.duplicate_cells == 0
    assert summary.status_counts == {STATUS_OFFICIAL_FAILED: 1, STATUS_OFFICIAL_PASSED: 1, STATUS_NO_REWARD: 1, STATUS_TIMEOUT: 1}

    families = summary.families
    assert families["click-test"] == {"value": pytest.approx(0.5), "passed": 1, "total": 2, "missing": []}
    assert families["enter-text"]["value"] == 0.0
    assert families["visual-addition"] == {"value": 0.0, "passed": 0, "total": 1, "missing": ["miniwob.visual-addition"]}
    assert summary.visual is not None and summary.visual.value == 0.0 and summary.visual.total == 1


def test_e20_cost_means_skip_none_and_count_it() -> None:
    """E20：token/cost/时长均值排除 None 且缺数计数正确（禁止记 0）。"""
    rows = [
        _metric_row(TASKS[0], total_tokens=1000, cost_usd=0.01, duration_s=10.0),
        _metric_row(TASKS[1], total_tokens=3000, cost_usd=0.03, duration_s=30.0),
        _metric_row(TASKS[2], total_tokens=None, cost_usd=None, duration_s=None),
    ]
    summary = metrics_module.summarize(rows, tasks=TASKS)
    cost = summary.cost
    assert cost is not None
    assert cost.runs == 3
    assert cost.avg_tokens == pytest.approx(2000.0)
    assert cost.total_tokens == 4000
    assert cost.tokens_missing_count == 1
    assert cost.avg_cost_usd == pytest.approx(0.02)
    assert cost.total_cost_usd == pytest.approx(0.04)
    assert cost.cost_missing_count == 1
    assert cost.avg_duration_s == pytest.approx(20.0)
    assert cost.total_duration_s == pytest.approx(40.0)
    assert cost.duration_missing_count == 1

    empty = metrics_module.summarize([], tasks=TASKS).cost
    assert empty is not None
    assert empty.avg_tokens is None and empty.total_tokens is None
    assert empty.avg_cost_usd is None and empty.total_cost_usd is None
    assert empty.avg_duration_s is None and empty.total_duration_s is None


def test_e20_disagreement_list_and_junit_side_rate() -> None:
    """E20：disagreement 清单（run_id/junit/official 三者）+ JUnit 侧对照通过率。"""
    rows = [
        _metric_row(TASKS[0], junit_passed=True, official_passed=True, disagreement=False),
        _metric_row(TASKS[1], status=STATUS_OFFICIAL_FAILED, official_passed=False, reward_raw=0, junit_passed=True, disagreement=True),
        _metric_row(TASKS[2], status=STATUS_OFFICIAL_PASSED, official_passed=True, reward_raw=1, junit_passed=False, disagreement=True),
        _metric_row(TASKS[3], status=STATUS_NO_JUNIT, official_passed=False, reward_raw=None, junit_passed=None, disagreement=None),
    ]
    summary = metrics_module.summarize(rows, tasks=TASKS)
    assert summary.disagreement_count == 2
    assert summary.disagreements == [
        {"run_id": "miniwob__click-test-2__s1", "task_id": "miniwob.click-test-2", "junit_passed": True, "official_passed": False},
        {"run_id": "miniwob__enter-text__s1", "task_id": "miniwob.enter-text", "junit_passed": False, "official_passed": True},
    ]
    # JUnit 侧：2 通过 / 5 任务；no_junit 与缺行各计 1 个不可用
    assert summary.junit is not None
    assert summary.junit.value == pytest.approx(2 / 5)
    assert summary.junit_unavailable_count == 2
    # 失败清单：两条非通过行（no_junit 行 + 官方失败行），缺行不出现
    assert [failure["task_id"] for failure in summary.failures] == ["miniwob.click-test-2", "miniwob.login-user"]
    assert summary.failures[0]["failure_message"] is None


def test_e20_retries_supersede_the_earlier_row_for_rates_but_not_for_cost() -> None:
    """E20：同一 cell 的重试行覆盖速率口径（last-wins），成本按每次执行累计。"""
    rows = [
        _metric_row(TASKS[0], status=STATUS_TIMEOUT, official_passed=False, reward_raw=None, total_tokens=None, cost_usd=None, duration_s=600.0),
        _metric_row(TASKS[0], status=STATUS_OFFICIAL_PASSED, official_passed=True),
    ]
    summary = metrics_module.summarize(rows, tasks=TASKS[:1])
    assert summary.cells == 1 and summary.duplicate_cells == 1
    assert summary.overall.value == 1.0
    assert summary.cost is not None and summary.cost.runs == 2 and summary.cost.tokens_missing_count == 1


def test_e20_summarize_defaults_to_the_full_task_table() -> None:
    """E20：未显式给任务表时使用入库的 tasks.json（125 任务分母）。"""
    summary = metrics_module.summarize([])
    assert summary.overall.total == 125
    assert summary.overall.value == 0.0
    assert len(summary.overall.missing) == 125
    assert len(summary.families) == 109
    assert summary.visual is not None and summary.visual.total == 1


def test_load_rows_tolerates_a_torn_tail(tmp_path: Path) -> None:
    """E 组补充：``load_rows`` 跳过空行与坏行（容错语义）。"""
    path = tmp_path / "results.jsonl"
    path.write_text('{"run_id": "a", "seed": 1}\n\nnot json\n   \n{"run_id": "b", "seed": 2}\n', encoding="utf-8")
    rows = metrics_module.load_rows(path)
    assert [r["run_id"] for r in rows] == ["a", "b"]
