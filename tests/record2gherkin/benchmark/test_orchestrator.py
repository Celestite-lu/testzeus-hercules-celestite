"""F 组：编排与护栏（spec §9 F21-F23）。

F21 cell 计划数与预算；F22 ``--dry-run`` 不进程/不预读/不写文件且打印计划；F23 断点跳过与 ``--force``。
全部用 monkeypatch 顶掉子进程与预读，离线可跑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark import tasks as tasks_module
from record2gherkin.benchmark.orchestrator import (
    HERCULES_BUDGET_CAP,
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
    """F21：pilot/full 计划数 = 10/125；预算 12/130，累计 142 = 红线；超限抛错。"""
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
    assert hercules_budget("pilot") == {"breakdown": {"pilot": 10}, "retry": 2, "total": 12, "cap": HERCULES_BUDGET_CAP}
    assert hercules_budget("full") == {"breakdown": {"full": 125}, "retry": 5, "total": 130, "cap": HERCULES_BUDGET_CAP}
    assert hercules_budget("pilot", "full")["total"] == 142 == HERCULES_BUDGET_CAP

    assert_budget(12)
    assert_budget(130)
    assert_budget(12 + 130)
    with pytest.raises(BenchmarkError):
        assert_budget(HERCULES_BUDGET_CAP + 1)
    with pytest.raises(BenchmarkError):
        assert_budget(12 + 130 + 1)
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
    assert manifest["budget"]["hercules_used"] == 0 and manifest["budget"]["cap"] == 142
    assert manifest["metrics"]["overall"]["total"] == 10
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
