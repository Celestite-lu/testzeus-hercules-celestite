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
