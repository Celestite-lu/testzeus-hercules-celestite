"""F 组：编排与护栏（spec §9 F21-F23）。

F21 cell 计划数与预算；F22 ``--dry-run`` 不进程/不预读/不写文件且打印计划；F23 断点跳过与 ``--force``。
全部用 monkeypatch 顶掉子进程与预读，离线可跑。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark import tasks as tasks_module
from record2gherkin.benchmark.orchestrator import (
    R2_BUDGET_CAP,
    RETRY_BUDGET,
    BenchmarkError,
    Cell,
    Orchestrator,
    assert_budget,
    hercules_budget,
    plan_cells,
)


def _forbid(name: str) -> Any:
    def _boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - only runs on a bug
        raise AssertionError(f"{name} must not be called on this path")

    return _boom


@pytest.fixture()
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real work (server, pre-read, Hercules) is forbidden on the dry-run path."""
    monkeypatch.setattr(orchestrator, "start_miniwob_server", _forbid("start_miniwob_server"))
    monkeypatch.setattr(orchestrator.goal_reader, "read_goal", _forbid("goal_reader.read_goal"))
    monkeypatch.setattr(orchestrator.runner_module, "run_feature", _forbid("runner.run_feature"))


def test_f21_stage_plan_sizes_and_budget_guard(tasks: list[dict[str, Any]]) -> None:
    """F21：pilot/full 计划数 = 10/125；预算 12/130，累计 142；r2 cap = 144（含 drag 冒烟 +2）；超限抛错。"""
    pilot = plan_cells("miniwob-pilot", "pilot", tasks=tasks)
    full = plan_cells("miniwob-full", "full", tasks=tasks)
    assert len(pilot) == 10
    assert len(full) == 125
    assert [cell.subdomain for cell in pilot] == list(tasks_module.PILOT_SUBDOMAINS)
    assert len({cell.seed for cell in pilot}) == 10
    assert len({cell.run_id for cell in pilot}) == 10
    assert {cell.task_id for cell in full} == {task["task_id"] for task in tasks}

    assert RETRY_BUDGET == {"pilot": 2, "full": 5}
    assert tasks_module.STAGE_RUNS == {"pilot": 10, "full": 125}
    assert R2_BUDGET_CAP == 144  # spec-r2 §0.2: 142 语义细化 + 冒烟 drag 2
    assert hercules_budget("pilot") == {"breakdown": {"pilot": 10}, "retry": 2, "total": 12, "cap": R2_BUDGET_CAP}
    assert hercules_budget("full") == {"breakdown": {"full": 125}, "retry": 5, "total": 130, "cap": R2_BUDGET_CAP}
    assert hercules_budget("pilot", "full")["total"] == 142
    assert hercules_budget("pilot", extra_runs=2) == {"breakdown": {"pilot": 10, "smoke_cells": 2}, "retry": 2, "total": 14, "cap": R2_BUDGET_CAP}
    assert hercules_budget("pilot", "full", extra_runs=2)["total"] == 144 == R2_BUDGET_CAP

    assert_budget(12)
    assert_budget(130)
    assert_budget(12 + 130)
    assert_budget(144)  # 恰好压线也放行
    with pytest.raises(BenchmarkError):
        assert_budget(R2_BUDGET_CAP + 1)
    with pytest.raises(BenchmarkError):
        assert_budget(12 + 130 + 2 + 1)
    with pytest.raises(BenchmarkError):
        hercules_budget("nope")
    with pytest.raises(BenchmarkError):
        Orchestrator("x", stage="nope", tasks=tasks)


def test_f22_dry_run_touches_nothing_and_prints_the_plan(tmp_path: Path, tasks: list[dict[str, Any]], caplog: pytest.LogCaptureFixture, no_subprocess: None) -> None:
    """F22：dry-run 无进程、无文件写入；打印项含 run_id、``?r2g_seed=`` URL、feature 路径、预算合计。"""
    exp_root = tmp_path / "dev_runs"
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    code = Orchestrator("miniwob-pilot", stage="pilot", exp_root=exp_root, tasks=tasks, dry_run=True).run()
    assert code == 0
    assert not exp_root.exists()
    assert list(tmp_path.rglob("*")) == []

    text = caplog.text
    first = plan_cells("miniwob-pilot", "pilot", tasks=tasks)[0]
    assert first.run_id in text
    assert f"?r2g_seed={first.seed}&r2g_ms={tasks_module.EPISODE_MAX_TIME_MS_DEFAULT}" in text
    assert str(exp_root / "miniwob-pilot" / "features" / f"{first.run_id}.feature") in text
    assert "budget" in text and '"total": 12' in text
    assert text.count("orchestrator: miniwob.") == 10


def test_f22_cli_dry_run_exit_code_and_no_files(tmp_path: Path, caplog: pytest.LogCaptureFixture, no_subprocess: None) -> None:
    """F22 补充：CLI 走 ``main()`` 同样安全退出（0）、不写任何文件、125 个 cell 全打印。"""
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    code = orchestrator.main(["--exp-id", "miniwob-full", "--stage", "full", "--dry-run", "--exp-root", str(tmp_path / "dev_runs")])
    assert code == 0
    assert not (tmp_path / "dev_runs").exists()
    assert caplog.text.count("orchestrator: miniwob.") == 125


def test_f23_resume_skips_recorded_cells_and_force_restores_them(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """F23：已有 results.jsonl 行的 cell 直接跳过（计划数递减）；``--force`` 恢复全量。"""
    exp_root = tmp_path / "dev_runs"
    exp_dir = exp_root / "miniwob-pilot"
    exp_dir.mkdir(parents=True)
    recorded = plan_cells("miniwob-pilot", "pilot", tasks=tasks)[0]
    row = {"task_id": recorded.task_id, "seed": recorded.seed, "status": "official_passed", "official_passed": True}
    (exp_dir / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    resume = Orchestrator("miniwob-pilot", stage="pilot", exp_root=exp_root, tasks=tasks, dry_run=True)
    existing = resume._existing_cells()
    assert existing == [(recorded.task_id, recorded.seed)]
    assert len(plan_cells("miniwob-pilot", "pilot", tasks=tasks, existing_keys=existing)) == 9

    forced = Orchestrator("miniwob-pilot", stage="pilot", exp_root=exp_root, tasks=tasks, dry_run=True, force=True)
    assert len(plan_cells("miniwob-pilot", "pilot", tasks=tasks, existing_keys=forced._existing_cells(), force=forced.force)) == 10

    # 别的 exp 不受影响（断点跳过按 exp 目录隔离）
    other = Orchestrator("miniwob-full", stage="full", exp_root=exp_root, tasks=tasks, dry_run=True)
    assert other._existing_cells() == []
    assert len(plan_cells("miniwob-full", "full", tasks=tasks, existing_keys=other._existing_cells())) == 125


def test_f23_infrastructure_retry_buffer_and_no_rerun_of_failures(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """F 组补充：重试只覆盖 timeout/no_junit/no_reward（用例失败/无 goal 绝不重跑），每 cell 至多 1 次，且不超过缓冲。"""
    assert orchestrator.INFRA_STATUSES == {"timeout", "no_junit", "no_reward"}
    assert orchestrator.INFRA_RETRY_LIMIT_PER_CELL == 1
    assert "official_failed" not in orchestrator.INFRA_STATUSES
    assert "no_goal" not in orchestrator.INFRA_STATUSES

    view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    pilot = plan_cells("miniwob-pilot", "pilot", tasks=tasks)
    rows: list[dict[str, Any]] = [
        {"task_id": pilot[0].task_id, "seed": pilot[0].seed, "status": "timeout"},
        {"task_id": pilot[1].task_id, "seed": pilot[1].seed, "status": "timeout"},
        {"task_id": pilot[1].task_id, "seed": pilot[1].seed, "status": "timeout"},  # 已重试过 → 不再重试
        {"task_id": pilot[2].task_id, "seed": pilot[2].seed, "status": "no_junit"},
        {"task_id": pilot[3].task_id, "seed": pilot[3].seed, "status": "official_failed"},
        {"task_id": pilot[4].task_id, "seed": pilot[4].seed, "status": "no_goal"},
        {"task_id": pilot[5].task_id, "seed": pilot[5].seed, "status": "no_reward"},  # 缓冲耗尽 → 保留原行
    ]
    view.results_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    retried: list[Any] = []
    monkeypatch.setattr(view, "_run_cell", lambda cell: retried.append(cell) or {})
    view._retry_infrastructure_failures()

    assert [cell.task_id for cell in retried] == [pilot[0].task_id, pilot[2].task_id]
    assert view.retries_used == RETRY_BUDGET["pilot"] == 2
    assert orchestrator.DEFAULT_TIMEOUT_S == 600
    assert tasks_module.EPISODE_MAX_TIME_MS_DEFAULT == 240000


def test_f23_cli_maps_unusable_executor_environment_to_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """F23 补充：执行环境不可用（缺 key 等 ``RunnerError``）→ 退出码 2 且只留一行错误日志。"""
    caplog.set_level(logging.ERROR, logger="testzeus_hercules.utils.logger")

    def _boom(self: Any) -> int:
        raise orchestrator.runner_module.RunnerError("cannot read LLM key")

    monkeypatch.setattr(Orchestrator, "run", _boom)
    code = orchestrator.main(["--exp-id", "miniwob-pilot", "--stage", "pilot", "--exp-root", str(tmp_path)])
    assert code == 2
    assert "cannot read LLM key" in caplog.text
    assert "Traceback" not in caplog.text


def test_f23_manifest_fields_and_no_secrets(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """F 组补充：manifest 字段集合完整（spec §7.4），且文本中不含 key/前缀。"""
    exp_root = tmp_path / "dev_runs"
    orchestrator_view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=exp_root, tasks=tasks)
    orchestrator_view.exp_dir.mkdir(parents=True, exist_ok=True)
    orchestrator_view._write_manifest()
    manifest = json.loads(orchestrator_view.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "exp_id",
        "git_rev",
        "started_at",
        "stage",
        "model_name",
        "llm_base_url",
        "server_port",
        "episode_max_time_ms",
        "timeout_s",
        "flags",
        "budget",
        "cells",
        "metrics",
        "finished_at",
    }
    assert manifest["stage"] == "pilot"
    assert manifest["server_port"] == 8462
    assert manifest["episode_max_time_ms"] == 240000
    assert manifest["timeout_s"] == 600
    assert len(manifest["cells"]) == 10
    assert manifest["budget"]["hercules_used"] == 0 and manifest["budget"]["cap"] == 144
    assert manifest["metrics"]["overall"]["total"] == 10
    # 默认运行：flags 全 false（nav_model/smoke_cells 为记录字段）；r3 起新增五键、默认 off/空语义
    # （spec-r3 §7；r2 八键断言按 spec 增量适配）。r4 增量（spec-r4 §4）：三个新键 + nav_max_tokens
    # 仅在实际传参时出现（默认缺省，避免"声明在位"失真）。
    assert manifest["flags"] == {
        "terminal_cue": False,
        "single_start": False,
        "role_routing": False,
        "nav_model": "deepseek-flash",
        "extra_tools": False,
        "template_notes": False,
        "latency_env": False,
        "smoke_cells": [],
        "planner_timeout": 0,
        "extra_tools_modules": [],
        "disable_sandbox": False,
        "assert_discipline": False,
        "md_interactive_extended": False,
        "verify_before_done": False,
        "offseed_beacon": True,
    }
    assert "nav_max_tokens" not in manifest["flags"]
    assert "api_key" not in json.dumps(manifest).lower()
    assert "redacted" not in json.dumps(manifest).lower()


# ---------------------------------------------------------------------------------------------
# F24/F25 cell 收尾完整性扫描（安全审查 R1 §4.2/§4.3 = H2 + H3；spec §7.1/§7.6）
# ---------------------------------------------------------------------------------------------

#: 每个真实 stdout.log 里都出现的工具注册行——绝不能作为沙箱**调用**证据（否则每 cell 误判）。
SANDBOX_REGISTRATION_LINES = (
    "[2026-09-21 23:02:41] INFO {langchain_tools.py:166} - [TOOL_DEBUG] Processing tool 'execute_python_sandbox' for agent 'executor_nav_agent'",
    "[2026-09-21 23:02:41] INFO {base_nav_agent.py:131} - Registered tool: execute_python_sandbox",
)


def _log_text(subdomain: str, seed: int, navigations: int = 1, *, extra_lines: tuple[str, ...] = ()) -> str:
    """One realistic child ``stdout.log`` (header + registration lines + ``Opening URL`` lines)."""
    lines = ["# run_id: miniwob__x", "# timed_out: False", "# returncode: 0", "", *SANDBOX_REGISTRATION_LINES]
    for _ in range(navigations):
        lines.append(f"[2026-09-21 23:02:50] INFO {{open_url.py:29}} - Opening URL: http://127.0.0.1:8462/miniwob/{subdomain}.html?r2g_seed={seed}&r2g_ms=240000 (force_new_tab=False)")
    lines.extend(extra_lines)
    return "\n".join(lines) + "\n"


def _scan_orchestrator(tmp_path: Path, tasks: list[dict[str, Any]]) -> tuple[Orchestrator, Cell]:
    view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    return view, plan_cells("miniwob-pilot", "pilot", tasks=tasks)[0]


def test_f24_renavigation_count_flags_but_never_rewrites_the_verdict(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """F24（H2/V4）：任务 URL 导航次数入行；>1 → flagged；官方奖励与 status 一字不改。"""
    view, cell = _scan_orchestrator(tmp_path, tasks)
    log_path = view._stdout_log_path(cell)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_path.write_text(_log_text(cell.subdomain, cell.seed, 1), encoding="utf-8")
    once = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert once == orchestrator.CellScan(task_url_navigations=1, reward_records=0, flagged=False, invalid_reason=None)

    log_path.write_text(_log_text(cell.subdomain, cell.seed, 2), encoding="utf-8")
    twice = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert twice.task_url_navigations == 2 and twice.flagged is True
    assert twice.invalid_reason is None  # V4 = 披露，不是无效（口径 5 保留官方奖励）

    # 其它 seed / 其它任务的同一行不计入本 cell（同一 stdout.log 内也绝不串号）
    other = _log_text(cell.subdomain, cell.seed + 1, 3) + _log_text("click-button", cell.seed, 3)
    log_path.write_text(other, encoding="utf-8")
    isolated = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert isolated.task_url_navigations == 0 and isolated.flagged is False

    # 集成：#2 次导航的 run 落行时字段与判定都在，且 official_passed 仍由奖励决定
    log_path.write_text(_log_text(cell.subdomain, cell.seed, 2), encoding="utf-8")
    monkeypatch.setattr(
        orchestrator.runner_module,
        "run_feature",
        lambda *args, **kwargs: orchestrator.runner_module.RunResult(
            run_id=cell.run_id, status="passed", passed=False, duration_s=42.0, cost_usd=0.01, total_tokens=1000, junit_xml=None, failure_message=None, run_dir=str(view.runs_dir / cell.run_id)
        ),
    )
    monkeypatch.setattr(orchestrator, "fetch_reward", lambda *args, **kwargs: {"path": f"/miniwob/{cell.subdomain}.html", "seed": str(cell.seed), "raw": 1, "done": True, "reason": ""})
    row = view._execute_cell(cell, goal="Click the button.", started_at="2026-09-21T00:00:00+00:00", started=0.0)
    assert row["task_url_navigations"] == 2
    assert row["flagged"] is True and row["invalid_reason"] is None
    assert row["status"] == "official_passed" and row["official_passed"] is True
    assert row["reward_raw"] == 1.0


def test_f24_file_url_and_sandbox_hits_invalidate_the_cell(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """F24（H2/V5+V6）：``file://`` 与沙箱**调用**命中 → cell 无效 + 安全事件；注册行绝不误判。"""
    view, cell = _scan_orchestrator(tmp_path, tasks)
    log_path = view._stdout_log_path(cell)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 只有注册行 → 中性（每个真实 run 的日志都长这样）
    log_path.write_text(_log_text(cell.subdomain, cell.seed, 1), encoding="utf-8")
    assert orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed).flagged is False

    # V5：本地文件逃逸（agent 打开 file:// 后 innerText 会把文件内容带进 LLM 上下文）
    file_url = _log_text(cell.subdomain, cell.seed, 1, extra_lines=("[2026-09-21 23:03:00] INFO {open_url.py:29} - Opening URL: file:///tmp/r2g_audit/secret_probe.txt (force_new_tab=False)",))
    log_path.write_text(file_url, encoding="utf-8")
    scan = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert scan.flagged is True and scan.invalid_reason == orchestrator.INVALID_REASON_FILE_URL

    # V6：沙箱真的被调用（两行调用标记都在 execute_python_sandbox.py 里）
    sandbox = _log_text(
        cell.subdomain,
        cell.seed,
        1,
        extra_lines=(
            "[2026-09-21 23:04:00] INFO {execute_python_sandbox.py:62} - Executing Python sandbox: file=/tmp/x.py, timeout=30s",
            "[2026-09-21 23:04:00] INFO {execute_python_sandbox.py:70} - Using sandbox tenant: default (no tenant)",
        ),
    )
    log_path.write_text(sandbox, encoding="utf-8")
    scan = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert scan.flagged is True and scan.invalid_reason == orchestrator.INVALID_REASON_SANDBOX

    # 两个安全事件同时命中 → 两个理由都在（排序去重，便于报告按字符串统计）
    both = file_url + "\n" + sandbox
    log_path.write_text(both, encoding="utf-8")
    scan = orchestrator.scan_cell_log(log_path, subdomain=cell.subdomain, seed=cell.seed)
    assert scan.invalid_reason == f"{orchestrator.INVALID_REASON_FILE_URL}; {orchestrator.INVALID_REASON_SANDBOX}"

    # 日志缺失 → 中性（没有证据绝不下结论）
    assert orchestrator.scan_cell_log(tmp_path / "missing.log", subdomain=cell.subdomain, seed=cell.seed) == orchestrator.CellScan()


def test_f24_merge_combines_both_scans() -> None:
    """F24 补充：``CellScan.merge`` 字段级合并（导航取大、flagged 取或、理由排序去重）。"""
    from record2gherkin.benchmark.orchestrator import CellScan

    left = CellScan(task_url_navigations=2, reward_records=1, flagged=True, invalid_reason=None)
    right = CellScan(task_url_navigations=0, reward_records=2, flagged=True, invalid_reason="b")
    merged = left.merge(right)
    assert merged == CellScan(task_url_navigations=2, reward_records=2, flagged=True, invalid_reason="b")
    assert CellScan().merge(CellScan()) == CellScan()
    assert CellScan(invalid_reason="b").merge(CellScan(invalid_reason="a")).invalid_reason == "a; b"


def test_f25_reward_record_count_reason_domain_and_consistency(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """F25（H3/V3）：行数超过页面加载数、reason 出域、done/raw 不自洽 → flagged；合法流不误判。"""
    view, cell = _scan_orchestrator(tmp_path, tasks)
    rewards = view.rewards_path
    rewards.parent.mkdir(parents=True, exist_ok=True)

    def _record(**overrides: Any) -> dict[str, Any]:
        payload = {"path": f"/miniwob/{cell.subdomain}.html", "seed": str(cell.seed), "reward": 1, "raw": 1, "done": True, "reason": "", "received_at": "2026-09-21T00:00:30+00:00"}
        payload.update(overrides)
        return payload

    def _write(*records: dict[str, Any]) -> None:
        rewards.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def _scan(**overrides: Any) -> orchestrator.CellScan:
        return orchestrator.scan_cell_rewards(rewards, subdomain=cell.subdomain, seed=cell.seed, page_loads=1, **overrides)

    _write(_record())
    assert _scan() == orchestrator.CellScan(task_url_navigations=0, reward_records=1, flagged=False, invalid_reason=None)

    # 记录数 > 页面加载数（伪造/额外 POST；1 次加载却落 2 行）
    _write(_record(), _record())
    assert _scan().flagged is True and _scan().reward_records == 2

    # reason 出域（合法域只有 '' / 'timed out' / unicode-test 的两个值）
    _write(_record(reason="audit-forgery"))
    assert _scan().flagged is True
    for legal in ("", "timed out", "Cool!", "You clicked on X when you should have clicked on Y"):
        _write(_record(reason=legal))
        assert _scan().flagged is False, legal

    # done/raw 不自洽（reward hook 只会写 done=true + 数字 raw）
    for bad in (_record(done=False), _record(raw=None), _record(raw=True), _record(raw="1")):
        _write(bad)
        assert _scan().flagged is True, bad

    # 页面自身超时记录合法（raw=-1, reason='timed out'）
    _write(_record(reward=-1, raw=-1, reason="timed out"))
    assert _scan().flagged is False

    # 其它 cell 的记录（同任务不同 seed / 同 seed 不同任务）不参与本 cell 判定
    _write(_record(seed=str(cell.seed + 1)), {"path": "/miniwob/click-button.html", "seed": str(cell.seed), "raw": 1, "done": True, "reason": ""})
    assert _scan() == orchestrator.CellScan()

    # 重试窗口：上一轮 attempt 的历史记录（received_at 在这一轮开始之前）不计入本轮的判定
    _write(_record(received_at="2026-09-21T00:00:10+00:00"), _record())
    windowed = _scan(started_at="2026-09-21T00:00:20+00:00", finished_at="2026-09-21T00:10:00+00:00")
    assert windowed.reward_records == 1 and windowed.flagged is False
    # 没有窗口（缺 started_at/finished_at）时两条都算 → 按行数异常 flagged（宁多勿漏）
    assert _scan().reward_records == 2 and _scan().flagged is True

    # 伪造记录照样不改判（only flagged）：集成行里 official_passed 仍由奖励本身决定
    _write(_record(reason="audit-forgery"))
    log_path = view._stdout_log_path(cell)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(_log_text(cell.subdomain, cell.seed, 1), encoding="utf-8")
    scan = view._scan_cell(cell, started_at=None, finished_at=None)
    assert scan.flagged is True and scan.invalid_reason is None and scan.reward_records == 1


# ---------------------------------------------------------------------------------------------
# r2 T3：C1b 熔断器（spec-r2 §2.2）
# ---------------------------------------------------------------------------------------------


def _runner_result(status: str, duration_s: float | None) -> Any:
    return orchestrator.runner_module.RunResult(
        run_id="miniwob__x__s1",
        status=status,
        passed=False,
        duration_s=duration_s,
        cost_usd=None,
        total_tokens=None,
        junit_xml=None,
        failure_message=None,
        run_dir="/tmp/run",
    )


def test_t3_attempt_breaker_needs_all_three_conditions(tmp_path: Path) -> None:
    """T3：infra 状态 + <30s + marker 命中三者缺一不可；日志缺失 → 不熔断（无证据不下结论）。"""
    log = tmp_path / "stdout.log"
    log.write_text("litellm.AuthenticationError: Error code: 402 - Insufficient Balance\n", encoding="utf-8")
    hit = orchestrator._attempt_circuit_break(_runner_result("no_junit", 4.2), stdout_log_path=log)
    assert hit is True
    assert orchestrator._attempt_circuit_break(_runner_result("timeout", 29.9), stdout_log_path=log) is True

    for marker in ("insufficient_user_balance", "insufficient_quota", "APIConnectionError", "Connection error"):
        log.write_text(f"boom: {marker}\n", encoding="utf-8")
        assert orchestrator._attempt_circuit_break(_runner_result("no_junit", 4.2), stdout_log_path=log) is True, marker

    log.write_text("plain failure without markers\n", encoding="utf-8")
    assert orchestrator._attempt_circuit_break(_runner_result("no_junit", 4.2), stdout_log_path=log) is False  # marker 不命中
    assert orchestrator._attempt_circuit_break(_runner_result("no_junit", 30.0), stdout_log_path=log) is False  # 不够快
    assert orchestrator._attempt_circuit_break(_runner_result("no_junit", None), stdout_log_path=log) is False
    assert orchestrator._attempt_circuit_break(_runner_result("passed", 4.2), stdout_log_path=log) is False  # 非 infra 状态
    assert orchestrator._attempt_circuit_break(_runner_result("no_junit", 4.2), stdout_log_path=tmp_path / "missing.log") is False


def _breaker_view(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, *, status: str = "no_junit") -> Orchestrator:
    """An orchestrator whose Hercules child is a fast infra death; the caller pre-writes breaker logs."""
    view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    for cell in plan_cells("miniwob-pilot", "pilot", tasks=tasks)[:3]:
        log_path = view._stdout_log_path(cell)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("litellm error: 402 Insufficient Balance\n", encoding="utf-8")

    def fake_run(feature_path: Path, **kwargs: Any) -> Any:
        return _runner_result(status, 4.2)

    monkeypatch.setattr(orchestrator.runner_module, "run_feature", fake_run)
    monkeypatch.setattr(orchestrator, "fetch_reward", lambda *args, **kwargs: None)
    return view


def test_t3_broken_rows_skip_the_retry_pool_and_keep_their_status(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T3：熔断行 status 仍按 §1.1（不改写），行内 infra_circuit_break=True 且不进重试池；未命中 → 照常重试。"""
    pilot = plan_cells("miniwob-pilot", "pilot", tasks=tasks)
    view = _breaker_view(tmp_path, tasks, monkeypatch)
    broken = view._execute_cell(pilot[0], goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert broken["status"] == "no_junit"  # 熔断不改写判分状态
    assert broken["infra_circuit_break"] is True and broken["attempt"] == 1
    view._append_row(broken)

    retried: list[Any] = []
    monkeypatch.setattr(view, "_run_cell", lambda cell: retried.append(cell) or {})
    view._retry_infrastructure_failures()
    assert [cell.task_id for cell in retried] == []  # 熔断行被显式排除

    # 对照：marker 不命中的 no_junit 行照常进重试池
    clean_log = view._stdout_log_path(pilot[1])
    clean_log.write_text("ordinary timeout output\n", encoding="utf-8")
    clean = view._execute_cell(pilot[1], goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert clean["infra_circuit_break"] is False
    view._append_row(clean)
    retried.clear()
    view._retry_infrastructure_failures()
    assert [cell.task_id for cell in retried] == [pilot[1].task_id]


def test_t3_two_consecutive_breaks_abort_the_stage(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T3：连续 2 格熔断 → BenchmarkError（exit 2），且一行的已写数据保留；非熔断行清零计数。"""
    pilot = plan_cells("miniwob-pilot", "pilot", tasks=tasks)
    view = _breaker_view(tmp_path, tasks, monkeypatch)
    monkeypatch.setattr(orchestrator.goal_reader, "read_goal", lambda *args, **kwargs: "goal text")

    view._run_cell(pilot[0])  # 熔断 #1：仅计数
    assert view.consecutive_breaks == 1
    assert view.results_path.is_file() and len(metrics_rows(view)) == 1
    with pytest.raises(BenchmarkError):
        view._run_cell(pilot[1])  # 熔断 #2 → 中止
    assert len(metrics_rows(view)) == 2  # 已写行保留

    # 非熔断行清零： breaker → clean → breaker 不触发中止
    view2 = _breaker_view(tmp_path / "reset", tasks, monkeypatch)
    monkeypatch.setattr(orchestrator.goal_reader, "read_goal", lambda *args, **kwargs: "goal text")
    view2._run_cell(pilot[0])
    clean_log = view2._stdout_log_path(pilot[1])
    clean_log.write_text("ordinary\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator.runner_module, "run_feature", lambda *args, **kwargs: _runner_result("no_junit", 120.0))  # 慢 → 非熔断
    view2._run_cell(pilot[1])
    assert view2.consecutive_breaks == 0
    monkeypatch.setattr(orchestrator.runner_module, "run_feature", lambda *args, **kwargs: _runner_result("no_junit", 4.2))
    view2._run_cell(pilot[2])  # 熔断 #1（重新计数）→ 不中止
    assert view2.consecutive_breaks == 1


def metrics_rows(view: Orchestrator) -> list[dict[str, Any]]:
    from record2gherkin.benchmark import metrics as metrics_module

    return metrics_module.load_rows(view.results_path)


# ---------------------------------------------------------------------------------------------
# r2 T4：C1c 余额预检（spec-r2 §3）
# ---------------------------------------------------------------------------------------------


FAKE_KEY = "sk-test-abcdefabcdefabcdef"


def test_t4_probe_success_402_and_connection_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """T4：ChatOpenAI.invoke（引擎同栈 transport 面）三路 —— 成功/402/连接错误；detail 全脱敏。"""
    from record2gherkin.benchmark import preflight as preflight_module

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", lambda self, *args, **kwargs: "ok")
    ok = preflight_module.probe_llm(api_key=FAKE_KEY, model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    assert ok == preflight_module.ProbeResult(ok=True, detail="ok")

    def insufficient(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"Error code: 402 - Insufficient Balance (key {FAKE_KEY})")

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", insufficient)
    out = preflight_module.probe_llm(api_key=FAKE_KEY, model="m", base_url="https://x")
    assert out.ok is False
    assert FAKE_KEY not in out.detail and orchestrator.runner_module.REDACTED in out.detail
    assert "402" in out.detail  # 原因可读（且不含 key）

    def connection(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("APIConnectionError: connection aborted")

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", connection)
    assert preflight_module.probe_llm(api_key=FAKE_KEY, model="m", base_url="https://x").ok is False


def test_t4_preflight_failure_exit_3_and_no_side_effects(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """T4：预检不过 → exit 3、不启动服务、不建目录；日志含脱敏原因与充值提示。"""
    from record2gherkin.benchmark import preflight as preflight_module

    def insufficient(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"402 Insufficient Balance {FAKE_KEY}")

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", insufficient)
    monkeypatch.setattr(orchestrator, "start_miniwob_server", _forbid("start_miniwob_server"))
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)  # 探测用的 key 即被脱敏的 key
    caplog.set_level(logging.ERROR, logger="testzeus_hercules.utils.logger")

    view = Orchestrator("miniwob-r2", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    assert view.run() == 3
    assert not (tmp_path / "dev_runs").exists()  # 无目录、无文件、无进程
    assert FAKE_KEY not in caplog.text
    assert "402" in caplog.text and "充值" in caplog.text


def test_t4_preflight_probes_both_models_only_with_role_routing(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T4：headline（role-routing on）探测 planner+nav 两个模型；off 时只探 planner 模型。"""
    from record2gherkin.benchmark import preflight as preflight_module

    probed: list[str] = []
    real_probe = preflight_module.probe_llm

    def spy(*, api_key: str, model: str, base_url: str, timeout_s: float = 30.0) -> Any:
        probed.append(model)
        return real_probe(api_key=api_key, model=model, base_url=base_url, timeout_s=timeout_s)

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", lambda self, *args, **kwargs: "ok")
    monkeypatch.setattr(orchestrator.preflight_module, "probe_llm", spy)

    single = Orchestrator("e1", stage="pilot", exp_root=tmp_path / "a", tasks=tasks)
    assert single._run_preflight() == 0
    assert probed == [orchestrator.runner_module.LLM_MODEL_NAME]

    probed.clear()
    routed = Orchestrator("e2", stage="pilot", exp_root=tmp_path / "b", tasks=tasks, role_routing=True, nav_model="deepseek-flash")
    assert routed._run_preflight() == 0
    assert probed == [orchestrator.runner_module.LLM_MODEL_NAME, "deepseek-flash"]

    # 缺 key 文件 → 同样 exit 3（ RunnerError 路径）
    def no_key(path: Any = None) -> str:
        raise orchestrator.runner_module.RunnerError("cannot read LLM key")

    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", no_key)
    assert Orchestrator("e3", stage="pilot", exp_root=tmp_path / "c", tasks=tasks)._run_preflight() == 3


def test_t4_dry_run_never_probes(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T4：``--dry-run`` 天然豁免预检（不探测、不读 key）。"""
    from record2gherkin.benchmark import preflight as preflight_module

    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", _forbid("ChatOpenAI.invoke"))
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", _forbid("read_api_key"))
    view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, dry_run=True)
    assert view.run() == 0


# ---------------------------------------------------------------------------------------------
# r2 T10：per-attempt 分目录日志（spec-r2 §2.1）
# ---------------------------------------------------------------------------------------------


class _FakePopen:
    """Minimal stand-in for the Hercules child: writes a fixed masked stdout, exits 0."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.returncode = 0
        self.pid = 4242

    def communicate(self, timeout: float | None = None) -> tuple[str, None]:
        return "hello from the fake hercules child", None


def test_t10_run_feature_writes_to_stdout_log_path_and_keeps_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T10：``stdout_log_path`` 落盘指定路径；缺省路径回归不变；掩码纪律保持。"""
    project = tmp_path / "opt"
    (project / "input").mkdir(parents=True)
    feature = project / "input" / "x.feature"
    feature.write_text("Feature: x\n  Scenario: s\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator.runner_module.subprocess, "Popen", _FakePopen)

    secret = "k" * 20
    custom = tmp_path / "attempt1" / "stdout.log"
    result = orchestrator.runner_module.run_feature(feature, run_id="r", project_root=project, api_key=secret, stdout_log_path=custom)
    assert result.status == orchestrator.runner_module.STATUS_NO_JUNIT  # 无 JUnit 产物 → no_junit
    assert custom.is_file() and "hello from the fake hercules child" in custom.read_text(encoding="utf-8")
    assert secret not in custom.read_text(encoding="utf-8")
    assert not (project.parent / "stdout.log").exists()  # 显式路径时不写默认位置

    default = orchestrator.runner_module.run_feature(feature, run_id="r", project_root=project, api_key=secret)
    assert default.status == orchestrator.runner_module.STATUS_NO_JUNIT
    assert (project.parent / "stdout.log").is_file()  # None → 历史默认 <run_dir>/stdout.log


def test_t10_orchestrator_passes_attempt_dirs_and_row_attempt(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T10：第二次 attempt 写 ``attempt2/``；scan_cell_log 只扫本 attempt 日志；结果行 attempt=2。"""
    pilot = plan_cells("miniwob-pilot", "pilot", tasks=tasks)
    view = Orchestrator("miniwob-pilot", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    view.results_path.parent.mkdir(parents=True, exist_ok=True)

    captured: dict[str, Any] = {}

    def fake_run(feature_path: Path, *, run_id: str = "", stdout_log_path: Any = None, **kwargs: Any) -> Any:
        captured["stdout_log_path"] = stdout_log_path
        return _runner_result("passed", 12.0)

    monkeypatch.setattr(orchestrator.runner_module, "run_feature", fake_run)
    monkeypatch.setattr(orchestrator, "fetch_reward", lambda *args, **kwargs: {"raw": 1, "done": True, "reason": ""})

    # 全新 cell → attempt1
    row1 = view._execute_cell(pilot[0], goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert str(captured["stdout_log_path"]).endswith(f"attempt1{os.sep}stdout.log")
    assert row1["attempt"] == 1
    view._append_row(row1)

    # 既有 1 行（timeout）→ 重试为 attempt2；scan_cell_log 只扫 attempt2 文件（写入 2 次导航）
    view.results_path.write_text(json.dumps({"task_id": pilot[1].task_id, "seed": pilot[1].seed, "status": "timeout"}) + "\n", encoding="utf-8")
    attempt2_log = view._stdout_log_path(pilot[1], attempt=2)
    attempt2_log.parent.mkdir(parents=True, exist_ok=True)
    attempt2_log.write_text(_log_text(pilot[1].subdomain, pilot[1].seed, 2), encoding="utf-8")
    row2 = view._execute_cell(pilot[1], goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert str(captured["stdout_log_path"]).endswith(f"attempt2{os.sep}stdout.log")
    assert row2["attempt"] == 2
    assert row2["task_url_navigations"] == 2  # H2 扫的是本 attempt 的日志文件


# ---------------------------------------------------------------------------------------------
# r2 T11：manifest flags 与 --smoke-cells 冒烟计划（spec-r2 §4.1/§0.2）
# ---------------------------------------------------------------------------------------------


def test_t11_default_flags_are_all_off(tasks: list[dict[str, Any]]) -> None:
    """T11：默认运行 flags 全 false；nav_model 记录默认值；smoke 清单为空；r3 五键 off/空（spec-r3 §7）。

    r4 适配（spec-r4 §4）：新增 md_interactive_extended/verify_before_done（off）与恒 true 的
    offseed_beacon；nav_max_tokens 未传参时键缺省。
    """
    view = Orchestrator("e", stage="pilot", tasks=tasks, dry_run=True)
    assert view.flags == {
        "terminal_cue": False,
        "single_start": False,
        "role_routing": False,
        "nav_model": "deepseek-flash",
        "extra_tools": False,
        "template_notes": False,
        "latency_env": False,
        "smoke_cells": [],
        "planner_timeout": 0,
        "extra_tools_modules": [],
        "disable_sandbox": False,
        "assert_discipline": False,
        "md_interactive_extended": False,
        "verify_before_done": False,
        "offseed_beacon": True,
    }
    assert "nav_max_tokens" not in view.flags


def test_t11_full_on_plan_is_12_cells_and_budget_14(tmp_path: Path, tasks: list[dict[str, Any]], caplog: pytest.LogCaptureFixture, no_subprocess: None) -> None:
    """T11：全开 + ``--smoke-cells drag-items,drag-box`` → pilot 计划恰 12 格（10+2 追加在尾部）、预算 14。"""
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    view = Orchestrator(
        "miniwob-r2",
        stage="pilot",
        exp_root=tmp_path / "dev_runs",
        tasks=tasks,
        dry_run=True,
        terminal_cue=True,
        single_start=True,
        role_routing=True,
        extra_tools=True,
        template_notes=True,
        latency_env=True,
        smoke_cells=["drag-items", "drag-box"],
    )
    assert view.flags["terminal_cue"] and view.flags["single_start"] and view.flags["role_routing"]
    assert view.flags["extra_tools"] and view.flags["template_notes"] and view.flags["latency_env"]
    assert view.flags["smoke_cells"] == ["drag-items", "drag-box"]
    code = view.run()
    assert code == 0
    text = caplog.text
    assert text.count("orchestrator: miniwob.") == 12
    assert "orchestrator: miniwob.drag-items seed=" in text and "orchestrator: miniwob.drag-box seed=" in text
    assert text.rindex("orchestrator: miniwob.drag-items seed=") > text.rindex("orchestrator: miniwob.visual-addition seed=")  # 追加格在 pilot 尾部
    assert '"total": 14' in text and '"cap": 144' in text


def test_t11_smoke_cells_rules(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """T11：smoke 仅允许 pilot；未知格/重复格硬失败；追加格不进重试池；已有行的追加格被断点跳过。"""
    with pytest.raises(BenchmarkError):
        Orchestrator("e", stage="full", tasks=tasks, smoke_cells=["drag-box"])
    unknown = Orchestrator("e", stage="pilot", tasks=tasks, smoke_cells=["no-such-task"])
    with pytest.raises(BenchmarkError):
        unknown.run()  # 未知格：不在任务表 → run() 即硬失败
    with pytest.raises(BenchmarkError):
        Orchestrator("e", stage="pilot", tasks=tasks, smoke_cells=["drag-box", "drag-box"])  # 重复格：配置错误硬失败

    # 追加格不进重试池
    orchestrator_view = Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, smoke_cells=["drag-box"])
    drag_box = orchestrator_view._smoke_cells_planned()[0]
    assert orchestrator_view._plan_stage_cells()[-1] == drag_box
    orchestrator_view.results_path.parent.mkdir(parents=True, exist_ok=True)
    pilot = plan_cells("e", "pilot", tasks=tasks)
    rows = [
        {"task_id": pilot[0].task_id, "seed": pilot[0].seed, "status": "timeout"},
        {"task_id": drag_box.task_id, "seed": drag_box.seed, "status": "timeout"},
    ]
    orchestrator_view.results_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    retried: list[Any] = []
    orchestrator_view._run_cell = lambda cell: retried.append(cell) or {}  # type: ignore[method-assign]
    orchestrator_view._retry_infrastructure_failures()
    assert [cell.task_id for cell in retried] == [pilot[0].task_id]  # drag-box 永不重试

    # 已有行的追加格在计划里被断点跳过（幂等）
    resumed = Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, smoke_cells=["drag-box"])
    assert all(cell.task_id != drag_box.task_id for cell in resumed._plan_stage_cells())


def test_t11_manifest_records_flags(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """T11：manifest ``flags`` 记录全部开关布尔、nav 模型与 smoke-cells 清单。"""
    view = Orchestrator(
        "miniwob-r2",
        stage="pilot",
        exp_root=tmp_path / "dev_runs",
        tasks=tasks,
        terminal_cue=True,
        single_start=True,
        role_routing=True,
        nav_model="deepseek-flash",
        extra_tools=True,
        template_notes=True,
        latency_env=True,
        smoke_cells=["drag-items", "drag-box"],
    )
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    view._write_manifest()
    manifest = json.loads(view.manifest_path.read_text(encoding="utf-8"))
    # r3 增量（spec-r3 §7）：extra_tools=True 时子集默认 ["drag_and_drop_tool"]，其余新键保持 off；
    # r4 增量（spec-r4 §4）：三个新键 + 未传参的 nav_max_tokens 键缺省
    assert manifest["flags"] == {
        "terminal_cue": True,
        "single_start": True,
        "role_routing": True,
        "nav_model": "deepseek-flash",
        "extra_tools": True,
        "template_notes": True,
        "latency_env": True,
        "smoke_cells": ["drag-items", "drag-box"],
        "planner_timeout": 0,
        "extra_tools_modules": ["drag_and_drop_tool"],
        "disable_sandbox": False,
        "assert_discipline": False,
        "md_interactive_extended": False,
        "verify_before_done": False,
        "offseed_beacon": True,
    }
    assert "nav_max_tokens" not in manifest["flags"]
    assert "api_key" not in json.dumps(manifest).lower()
