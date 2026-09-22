"""T1 — r1 data replay lock for the C1a judgement fix (spec-r2 §1.3).

The fixture ``fixtures/r1_replay.json`` is derived from the real r1 run
(``dev_runs/benchmark/miniwob-r1/results.jsonl``, 130 rows → 125 cells by last-wins) and holds only
the judgement inputs ``(runner_status, raw)`` — no key, no long text.  It locks:

* the 5 rescue cells flip to ``official_passed`` (login-user-popup, multi-layouts, use-colorwheel-2,
  click-pie, click-collapsible-2-nodelay);
* the counter-example email-inbox-delete stays **non**-official_passed (its r1 terminal state is
  ``runner_status=timeout, raw=-1`` so it lands on ``status=timeout`` — metrics judge 0 — review-r2
  M5-1: it is *not* the literal ``official_failed``);
* consistent and infra cells keep their verdicts;
* applying the rule to the 125-cell summary yields the official total 54 → **59** (+5, locked value).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from record2gherkin.benchmark import metrics as metrics_module
from record2gherkin.benchmark.orchestrator import (
    STATUS_NO_GOAL,
    STATUS_NO_REWARD,
    STATUS_OFFICIAL_FAILED,
    STATUS_OFFICIAL_PASSED,
    STATUS_TIMEOUT,
    build_result_row,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "r1_replay.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _task(task_id: str) -> dict[str, Any]:
    subdomain = task_id.removeprefix("miniwob.")
    return {"task_id": task_id, "subdomain": subdomain, "family": subdomain, "visual": False}


def _row_for(case: dict[str, Any]) -> dict[str, Any]:
    """One ``build_result_row`` call from the fixture's judgement inputs."""
    raw = case.get("raw")
    reward = {"raw": raw, "done": True, "reason": ""} if case.get("reward_present") else None
    junit_passed = case.get("junit_passed")
    return build_result_row(
        task=_task(str(case["task_id"])),
        seed=1,
        goal="do the task",
        episode_ms=240000,
        runner_status=case.get("runner_status"),
        reward=reward,
        junit_passed=junit_passed,
        junit_terminate=None,
        junit_xml=None,
        failure_message=None,
        duration_s=10.0,
        total_tokens=1000,
        cost_usd=0.01,
        started_at="2026-09-22T00:00:00+00:00",
        finished_at="2026-09-22T00:01:00+00:00",
    )


def test_t1_rescue_cells_flip_to_official_passed() -> None:
    """5 个救援格：引擎 infra 状态 + 页面 raw>0 → official_passed，且 runner_status 保留 infra 状态。"""
    rescues = [case for case in _fixture()["cases"] if case["kind"] == "rescue"]
    assert len(rescues) == 5
    for case in rescues:
        row = _row_for(case)
        assert row["status"] == STATUS_OFFICIAL_PASSED, case
        assert row["official_passed"] is True
        assert row["runner_status"] == case["runner_status"]  # "timeout" | "no_junit" — auditable
        assert row["reward_raw"] == float(case["raw"])


def test_t1_counterexample_stays_non_official_passed() -> None:
    """email-inbox-delete（r1: timeout / raw=-1 / nav=8）维持非 official_passed，落在 status=timeout。"""
    case = next(case for case in _fixture()["cases"] if case["kind"] == "counterexample")
    assert case["task_id"] == "miniwob.email-inbox-delete"
    row = _row_for(case)
    assert row["status"] == STATUS_TIMEOUT  # review-r2 M5-1: 非 official_passed，但不是 official_failed 字面值
    assert row["official_passed"] is False
    assert row["reward_raw"] == -1.0
    assert metrics_module._passed(row) == 0  # 指标判 0


def test_t1_consistent_and_infra_cells_keep_their_verdicts() -> None:
    """一致格（passed/failed/no_reward）与 infra 格（timeout/no_junit，raw<=0）判定不变。"""
    for kind in ("consistent", "infra"):
        for case in (c for c in _fixture()["cases"] if c["kind"] == kind):
            row = _row_for(case)
            assert row["status"] == case["expect_status"], (kind, case)
            assert row["official_passed"] is case["expect_official_passed"]


def test_t1_official_total_moves_54_to_59_over_the_full_r1_summary() -> None:
    """把 §1.1 规则作用于 r1 全量等价输入（125 格摘要）：官方合计 54 → 59。"""
    fixture = _fixture()
    summary = fixture["summary_125"]
    assert len(summary) == 125
    assert fixture["r1_official_total"] == 54
    assert fixture["r2_official_total"] == 59

    legacy = r2 = 0
    for runner_status, raw in summary:
        # r1 判定（老优先级链）：infra 状态压倒页面奖励；无 reward → no_reward；否则 raw>0。
        if runner_status not in ("timeout", "no_junit") and raw is not None and raw > 0:
            legacy += 1
        reward = {"raw": raw, "done": True, "reason": ""} if raw is not None else None
        row = build_result_row(
            task=_task("miniwob.probe"),
            seed=1,
            goal=None,
            episode_ms=240000,
            runner_status=runner_status,
            reward=reward,
            junit_passed=None,
            junit_terminate=None,
            junit_xml=None,
            failure_message=None,
            duration_s=1.0,
            total_tokens=None,
            cost_usd=None,
            started_at="2026-09-22T00:00:00+00:00",
            finished_at="2026-09-22T00:00:01+00:00",
        )
        r2 += 1 if row["official_passed"] else 0
    assert legacy == 54
    assert r2 == 59


def test_t1_boundary_cases_of_the_priority_chain() -> None:
    """边界：raw=0.537>0+timeout → passed；raw=-1+junit passed → official_failed+disagreement；no_goal 最高优先；无 reward 无 infra → no_reward。"""

    def row(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = dict(
            task=_task("miniwob.probe"),
            seed=7,
            goal="do it",
            episode_ms=240000,
            runner_status="passed",
            reward=None,
            junit_passed=None,
            junit_terminate=None,
            junit_xml=None,
            failure_message=None,
            duration_s=1.0,
            total_tokens=None,
            cost_usd=None,
            started_at="2026-09-22T00:00:00+00:00",
            finished_at="2026-09-22T00:00:01+00:00",
        )
        base.update(overrides)
        return build_result_row(**base)

    fractional = row(runner_status="timeout", reward={"raw": 0.537, "done": True, "reason": ""}, junit_passed=None)
    assert fractional["status"] == STATUS_OFFICIAL_PASSED and fractional["official_passed"] is True

    page_fail = row(runner_status="failed", reward={"raw": -1, "done": True, "reason": "timed out"}, junit_passed=True)
    assert page_fail["status"] == STATUS_OFFICIAL_FAILED
    assert page_fail["official_passed"] is False and page_fail["disagreement"] is True

    no_goal = row(runner_status=None, goal=None, reward={"raw": 1, "done": True, "reason": ""}, junit_passed=None)
    assert no_goal["status"] == STATUS_NO_GOAL and no_goal["runner_status"] is None  # 最高优先级

    no_reward = row(runner_status="failed", reward=None, junit_passed=False)
    assert no_reward["status"] == STATUS_NO_REWARD and no_reward["runner_status"] == STATUS_NO_REWARD
