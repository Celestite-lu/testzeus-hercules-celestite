"""Benchmark orchestration (spec §7): cells, server lifecycle, result rows, retries, manifest.

CLI::

    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-pilot --stage pilot
    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full
    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full --dry-run

Per cell the harness derives the seed, pre-reads the goal in its own browser, renders the feature,
runs Hercules in a sub-process (:mod:`record2gherkin.evaluation.runner`) and finally asks the patch
server for the terminal reward — the **only** authority for ``official_passed`` (spec §0 口径 4).
Every cell produces exactly one appended ``results.jsonl`` row; ``timeout`` / ``no_junit`` /
``no_reward`` cells may be retried once each (infrastructure only), a failed episode never is.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from record2gherkin.benchmark import goal_reader
from record2gherkin.benchmark import metrics as metrics_module
from record2gherkin.benchmark import tasks as tasks_module
from record2gherkin.benchmark.miniwob_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    HEALTH_PATH,
    REWARD_LATEST_PATH,
)
from record2gherkin.evaluation import runner as runner_module
from testzeus_hercules.utils.logger import logger

REPO_ROOT = runner_module.REPO_ROOT
MODULE_DIR = tasks_module.MODULE_DIR
MINIWOB_HTML_ROOT = MODULE_DIR / "miniwob_html"
DEFAULT_EXP_ROOT = REPO_ROOT / "dev_runs" / "benchmark"

#: spec §6: 240s episode + planner head-room.
DEFAULT_TIMEOUT_S = 600
SERVER_HEALTH_TIMEOUT_S = 15.0
#: spec §7.5: 142 Hercules executions is the cumulative red line of the whole benchmark.
HERCULES_BUDGET_CAP = 142
#: spec §7.5: infrastructure retry buffers (pilot 10+2, full 125+5).
RETRY_BUDGET: Mapping[str, int] = {"pilot": 2, "full": 5}
#: spec §7.5: retries cover infrastructure only, at most once per cell.
INFRA_RETRY_LIMIT_PER_CELL = 1
INFRA_STATUSES = frozenset({"timeout", "no_junit", "no_reward"})

STATUS_OFFICIAL_PASSED = "official_passed"
STATUS_OFFICIAL_FAILED = "official_failed"
STATUS_TIMEOUT = "timeout"
STATUS_NO_JUNIT = "no_junit"
STATUS_NO_REWARD = "no_reward"
STATUS_NO_GOAL = "no_goal"


class BenchmarkError(RuntimeError):
    """Orchestration error (busy port, server crash, budget breach, unusable task table)."""


@dataclass(frozen=True)
class Cell:
    """One benchmark cell: a task with its derived seed (spec §2.3)."""

    task_id: str
    subdomain: str
    family: str
    visual: bool
    seed: int

    @property
    def run_id(self) -> str:
        return tasks_module.make_run_id(self.subdomain, self.seed)

    @property
    def label(self) -> str:
        return f"{self.task_id}/s{self.seed}"


# ---------------------------------------------------------------------------------------------
# Cells, budget guard (spec §2.3, §7.5)
# ---------------------------------------------------------------------------------------------


def plan_cells(
    exp_id: str,
    stage: str,
    *,
    tasks: Sequence[Mapping[str, Any]] | None = None,
    existing_keys: Sequence[tuple[str, int]] = (),
    force: bool = False,
) -> list[Cell]:
    """Cells of one stage, minus the ones already present in ``results.jsonl`` (spec §7.3 断点跳过)."""
    table = list(tasks) if tasks is not None else tasks_module.load_tasks()
    stage_tasks = tasks_module.select_stage(stage, table)
    done = set() if force else {(str(task_id), int(seed)) for task_id, seed in existing_keys}
    cells: list[Cell] = []
    for task in stage_tasks:
        task_id = str(task["task_id"])
        seed = tasks_module.derive_seed(exp_id, task_id)
        if (task_id, seed) in done:
            continue
        cells.append(
            Cell(
                task_id=task_id,
                subdomain=str(task["subdomain"]),
                family=str(task["family"]),
                visual=bool(task["visual"]),
                seed=seed,
            )
        )
    return cells


def hercules_budget(*stages: str) -> dict[str, Any]:
    """Planned Hercules runs per stage plus the infrastructure retry buffer (spec §7.5)."""
    breakdown: dict[str, int] = {}
    retry = 0
    for stage in stages:
        if stage not in tasks_module.STAGE_RUNS:
            raise BenchmarkError(f"unknown stage: {stage!r} (expected one of {tasks_module.STAGES})")
        breakdown[stage] = tasks_module.STAGE_RUNS[stage]
        retry += RETRY_BUDGET[stage]
    total = sum(breakdown.values()) + retry
    return {"breakdown": breakdown, "retry": retry, "total": total, "cap": HERCULES_BUDGET_CAP}


def assert_budget(planned_runs: int, *, cap: int = HERCULES_BUDGET_CAP) -> None:
    """Guard rail of spec §7.5: the benchmark must never plan more than ``cap`` Hercules runs."""
    if planned_runs > cap:
        raise BenchmarkError(f"budget breach: {planned_runs} Hercules runs planned, cap is {cap}")


# ---------------------------------------------------------------------------------------------
# Result rows (spec §7.1/§7.2)
# ---------------------------------------------------------------------------------------------


def _reward_raw_value(reward: Mapping[str, Any] | None) -> float | None:
    if reward is None:
        return None
    value = reward.get("raw")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_result_row(
    *,
    task: Mapping[str, Any],
    seed: int,
    goal: str | None,
    episode_ms: int,
    runner_status: str | None,
    reward: Mapping[str, Any] | None,
    junit_passed: bool | None,
    junit_terminate: str | None,
    junit_xml: str | None,
    failure_message: str | None,
    duration_s: float | None,
    total_tokens: int | None,
    cost_usd: float | None,
    started_at: str,
    finished_at: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Assemble one ``results.jsonl`` row (spec §7.1) with the deterministic status rules of §7.2.

    ``runner_status=None`` means "not executed" (goal pre-read failed, §7.2.5).  The official verdict is
    ``reward_raw > 0`` and nothing else: a negative reward (the page's own ``timed out`` record is
    ``raw=-1``) is recorded as-is and still counts as an official failure.
    """
    if runner_status is None:
        status = STATUS_NO_GOAL
    elif runner_status == runner_module.STATUS_TIMEOUT:
        status = STATUS_TIMEOUT
    elif runner_status == runner_module.STATUS_NO_JUNIT:
        status = STATUS_NO_JUNIT
    elif reward is None:
        status = STATUS_NO_REWARD
    else:
        raw = _reward_raw_value(reward)
        status = STATUS_OFFICIAL_PASSED if (raw is not None and raw > 0) else STATUS_OFFICIAL_FAILED

    official_passed = status == STATUS_OFFICIAL_PASSED
    if status in (STATUS_OFFICIAL_PASSED, STATUS_OFFICIAL_FAILED):
        disagreement: bool | None = junit_passed is not None and junit_passed != official_passed
    else:
        disagreement = None

    subdomain = str(task["subdomain"])
    raw = _reward_raw_value(reward)
    return {
        "run_id": tasks_module.make_run_id(subdomain, seed),
        "task_id": str(task["task_id"]),
        "subdomain": subdomain,
        "family": str(task["family"]),
        "visual": bool(task["visual"]),
        "seed": seed,
        "goal": goal,
        "episode_max_time_ms": episode_ms,
        "status": status,
        "official_passed": official_passed,
        "reward_raw": raw,
        "done": reward.get("done") if reward is not None else None,
        "reward_reason": reward.get("reason") if reward is not None else None,
        "junit_passed": junit_passed,
        "junit_terminate": junit_terminate,
        "disagreement": disagreement,
        "duration_s": duration_s,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "junit_xml": junit_xml,
        "failure_message": failure_message,
        "started_at": started_at,
        "finished_at": finished_at,
        "model": model,
    }


# ---------------------------------------------------------------------------------------------
# Server + HTTP plumbing (spec §3.1/§3.3)
# ---------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe_port(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout_s: float = 1.0) -> bool:
    """True when something is already listening on ``host:port`` (spec §3.1: 启动前探测占用)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex((host, port)) == 0


#: 显式禁用代理：macOS 的系统代理会让 127.0.0.1 的回环请求走代理并返回 502（exp001 已知问题 11）。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, payload: Mapping[str, Any] | None = None, timeout_s: float = 5.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with _NO_PROXY_OPENER.open(request, timeout=timeout_s) as response:  # noqa: S310 - loopback only
        return json.loads(response.read().decode("utf-8"))


def fetch_reward(base_url: str, *, task: str, seed: int, timeout_s: float = 5.0) -> dict[str, Any] | None:
    """Terminal record of ``(task, seed)`` or ``None`` (404 ``no_reward``, spec §3.3)."""
    query = urllib.parse.urlencode({"task": task, "seed": seed})
    try:
        return http_json(f"{base_url}{REWARD_LATEST_PATH}?{query}", timeout_s=timeout_s)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise BenchmarkError(f"reward lookup failed for {task} seed={seed}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, ValueError) as exc:
        raise BenchmarkError(f"reward lookup failed for {task} seed={seed}: {exc}") from exc


def start_miniwob_server(
    *,
    root: Path = MINIWOB_HTML_ROOT,
    port: int = DEFAULT_PORT,
    rewards_file: Path,
    host: str = DEFAULT_HOST,
) -> subprocess.Popen[str]:
    """Start the patch server as a sub-process after probing the port (spec §3.1)."""
    if probe_port(host, port):
        raise BenchmarkError(f"port {host}:{port} is already in use; free it before the benchmark (the URL is an experiment parameter)")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "record2gherkin.benchmark.miniwob_server",
            "--root",
            str(root),
            "--host",
            host,
            "--port",
            str(port),
            "--rewards-file",
            str(rewards_file),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://{host}:{port}"
    deadline = time.monotonic() + SERVER_HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise BenchmarkError(f"miniwob server exited early (code {process.returncode}): {output[-2000:]}")
        try:
            http_json(f"{base_url}{HEALTH_PATH}")
            logger.info("orchestrator: miniwob server up at %s", base_url)
            return process
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.2)
    process.kill()
    raise BenchmarkError(f"miniwob server did not become healthy within {SERVER_HEALTH_TIMEOUT_S:.0f}s")


def stop_miniwob_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()


def git_rev(repo_root: Path = REPO_ROOT) -> str | None:
    """Current revision, read from ``.git`` files only (no git command is ever executed)."""
    git_dir = repo_root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        if ref:
            try:
                return (git_dir / ref).read_text(encoding="utf-8").strip() or None
            except OSError:
                pass
        try:
            for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                sha, _, name = line.partition(" ")
                if ref and name.strip() == ref:
                    return sha.strip()
        except OSError:
            return None
        return None
    return head or None


# ---------------------------------------------------------------------------------------------
# Orchestrator (spec §7.3/§7.4)
# ---------------------------------------------------------------------------------------------


class Orchestrator:
    """One exp's benchmark stage: results.jsonl rows, rewards.jsonl and the manifest."""

    def __init__(
        self,
        exp_id: str,
        *,
        stage: str,
        exp_root: Path = DEFAULT_EXP_ROOT,
        html_root: Path = MINIWOB_HTML_ROOT,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        episode_ms: int = tasks_module.EPISODE_MAX_TIME_MS_DEFAULT,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        dry_run: bool = False,
        force: bool = False,
        tasks: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        if stage not in tasks_module.STAGES:
            raise BenchmarkError(f"unknown stage: {stage!r} (expected one of {tasks_module.STAGES})")
        self.exp_id = exp_id
        self.stage = stage
        self.exp_dir = Path(exp_root) / exp_id
        self.runs_dir = self.exp_dir / "runs"
        self.features_dir = self.exp_dir / "features"
        self.results_path = self.exp_dir / "results.jsonl"
        self.rewards_path = self.exp_dir / "rewards.jsonl"
        self.manifest_path = self.exp_dir / "manifest.json"
        self.html_root = Path(html_root)
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.episode_ms = episode_ms
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.force = force
        self.tasks = [dict(task) for task in tasks] if tasks is not None else tasks_module.load_tasks()
        self.stage_tasks = tasks_module.select_stage(stage, self.tasks)
        self.started_at = _now()
        self.hercules_runs = 0
        self.retries_used = 0

    # -- public entry ------------------------------------------------------------------------

    def run(self) -> int:
        """Execute the stage; returns a process exit code."""
        budget = hercules_budget(self.stage)
        assert_budget(budget["total"])
        cells = plan_cells(self.exp_id, self.stage, tasks=self.tasks, existing_keys=self._existing_cells(), force=self.force)
        if self.dry_run:
            return self._print_plan(cells, budget)

        self.exp_dir.mkdir(parents=True, exist_ok=True)
        server = start_miniwob_server(root=self.html_root, port=self.port, rewards_file=self.rewards_path, host=self.host)
        try:
            for cell in cells:
                self._run_cell(cell)
            self._retry_infrastructure_failures()
        finally:
            stop_miniwob_server(server)
        self._write_manifest()
        logger.info(
            "orchestrator: stage %s finished, %s Hercules runs used, %s retries (cap %s)",
            self.stage,
            self.hercules_runs,
            self.retries_used,
            HERCULES_BUDGET_CAP,
        )
        return 0

    # -- plan / resume -----------------------------------------------------------------------

    def _existing_cells(self) -> list[tuple[str, int]]:
        """``(task_id, seed)`` pairs already present in ``results.jsonl`` (spec §7.3 断点跳过)."""
        if not self.results_path.is_file():
            return []
        keys: list[tuple[str, int]] = []
        for row in metrics_module.load_rows(self.results_path):
            seed = row.get("seed")
            if isinstance(seed, int) and not isinstance(seed, bool):
                keys.append((str(row.get("task_id")), seed))
        return keys

    def _print_plan(self, cells: Sequence[Cell], budget: Mapping[str, Any]) -> int:
        logger.info("orchestrator: dry-run for exp %s stage %s (%s cells)", self.exp_id, self.stage, len(cells))
        for cell in cells:
            url = goal_reader.page_url(cell.subdomain, port=self.port, seed=cell.seed, episode_ms=self.episode_ms)
            feature_path = self.features_dir / f"{cell.run_id}.feature"
            logger.info(
                "orchestrator: %s seed=%s run_id=%s feature=%s url=%s",
                cell.task_id,
                cell.seed,
                cell.run_id,
                feature_path,
                url,
            )
        logger.info("orchestrator: dry-run budget %s", json.dumps(budget, sort_keys=True))
        logger.info("orchestrator: dry-run wrote nothing (no process, no pre-read, no file)")
        return 0

    # -- one cell ----------------------------------------------------------------------------

    def _assert_can_run(self) -> None:
        limit = tasks_module.STAGE_RUNS[self.stage] + RETRY_BUDGET[self.stage]
        if self.hercules_runs >= min(limit, HERCULES_BUDGET_CAP):
            raise BenchmarkError(f"budget breach: refusing Hercules run {self.hercules_runs + 1} (stage limit {limit}, cap {HERCULES_BUDGET_CAP})")

    def _run_cell(self, cell: Cell) -> dict[str, Any]:
        started_at = _now()
        started = time.monotonic()
        try:
            goal = goal_reader.read_goal(cell.subdomain, cell.seed, port=self.port, episode_ms=self.episode_ms)
        except goal_reader.GoalReadError as exc:
            row = build_result_row(
                task=self._task_for(cell.task_id),
                seed=cell.seed,
                goal=None,
                episode_ms=self.episode_ms,
                runner_status=None,
                reward=None,
                junit_passed=None,
                junit_terminate=None,
                junit_xml=None,
                failure_message=f"no_goal: {exc}",
                duration_s=round(time.monotonic() - started, 3),
                total_tokens=None,
                cost_usd=None,
                started_at=started_at,
                finished_at=_now(),
                model=runner_module.LLM_MODEL_NAME,
            )
            self._append_row(row)
            logger.warning("orchestrator: %s -> no_goal (%s)", cell.label, exc)
            return row

        row = self._execute_cell(cell, goal=goal, started_at=started_at, started=started)
        self._append_row(row)
        logger.info("orchestrator: %s -> %s (%.1fs, reward=%s)", cell.run_id, row["status"], time.monotonic() - started, row["reward_raw"])
        return row

    def _execute_cell(self, cell: Cell, *, goal: str, started_at: str, started: float) -> dict[str, Any]:
        self._assert_can_run()
        feature_text = goal_reader.render_feature(
            task_id=cell.task_id,
            subdomain=cell.subdomain,
            seed=cell.seed,
            port=self.port,
            episode_ms=self.episode_ms,
            goal=goal,
        )
        self.features_dir.mkdir(parents=True, exist_ok=True)
        (self.features_dir / f"{cell.run_id}.feature").write_text(feature_text, encoding="utf-8")

        project_root = runner_module.prepare_run_dir(self.runs_dir / cell.run_id / "opt")
        feature_path = project_root / "input" / f"{cell.run_id}.feature"
        feature_path.write_text(feature_text, encoding="utf-8")

        self.hercules_runs += 1
        result = runner_module.run_feature(feature_path, run_id=cell.run_id, project_root=project_root, timeout_s=self.timeout_s)
        junit_passed, junit_terminate = _junit_verdict(result)
        reward = fetch_reward(self.base_url, task=cell.subdomain, seed=cell.seed)
        return build_result_row(
            task=self._task_for(cell.task_id),
            seed=cell.seed,
            goal=goal,
            episode_ms=self.episode_ms,
            runner_status=result.status,
            reward=reward,
            junit_passed=junit_passed,
            junit_terminate=junit_terminate,
            junit_xml=result.junit_xml,
            failure_message=result.failure_message,
            duration_s=result.duration_s,
            total_tokens=result.total_tokens,
            cost_usd=result.cost_usd,
            started_at=started_at,
            finished_at=_now(),
            model=runner_module.LLM_MODEL_NAME,
        )

    def _task_for(self, task_id: str) -> dict[str, Any]:
        for task in self.tasks:
            if str(task["task_id"]) == task_id:
                return dict(task)
        raise BenchmarkError(f"task not in the table: {task_id}")

    # -- retries / manifest ------------------------------------------------------------------

    def _retry_infrastructure_failures(self) -> None:
        """Re-run infrastructure cells (timeout/no_junit/no_reward) once each, within the retry buffer."""
        retry_budget = RETRY_BUDGET[self.stage]
        if retry_budget <= 0 or not self.results_path.is_file():
            return
        attempts: dict[tuple[str, int], int] = {}
        for row in metrics_module.load_rows(self.results_path):
            seed = row.get("seed")
            if not isinstance(seed, int) or isinstance(seed, bool):
                continue
            attempts[(str(row.get("task_id")), seed)] = attempts.get((str(row.get("task_id")), seed), 0) + 1
        infra_cells: list[Cell] = []
        for row in metrics_module.load_rows(self.results_path):
            if row.get("status") not in INFRA_STATUSES:
                continue
            seed = row.get("seed")
            if not isinstance(seed, int) or isinstance(seed, bool):
                continue
            if attempts.get((str(row.get("task_id")), seed), 0) > INFRA_RETRY_LIMIT_PER_CELL:
                continue
            try:
                cell = self._cell_for(str(row.get("task_id")), seed)
            except BenchmarkError:
                continue
            if cell not in infra_cells:
                infra_cells.append(cell)
        for cell in infra_cells:
            if self.retries_used >= retry_budget:
                logger.warning("orchestrator: retry buffer exhausted (%s), leaving %s as recorded data", retry_budget, cell.label)
                break
            self.retries_used += 1
            logger.info("orchestrator: infrastructure retry %s/%s for %s", self.retries_used, retry_budget, cell.label)
            self._run_cell(cell)

    def _cell_for(self, task_id: str, seed: int) -> Cell:
        task = self._task_for(task_id)
        return Cell(
            task_id=task_id,
            subdomain=str(task["subdomain"]),
            family=str(task["family"]),
            visual=bool(task["visual"]),
            seed=seed,
        )

    def _append_row(self, row: Mapping[str, Any]) -> None:
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()

    def _write_manifest(self) -> None:
        rows = metrics_module.load_rows(self.results_path) if self.results_path.is_file() else []
        summary = metrics_module.summarize(rows, tasks=self.stage_tasks, exp_id=self.exp_id)
        manifest = {
            "exp_id": self.exp_id,
            "git_rev": git_rev(),
            "started_at": self.started_at,
            "stage": self.stage,
            "model_name": runner_module.LLM_MODEL_NAME,
            "llm_base_url": runner_module.LLM_MODEL_BASE_URL,
            "server_port": self.port,
            "episode_max_time_ms": self.episode_ms,
            "timeout_s": self.timeout_s,
            "budget": {
                "hercules_used": self.hercules_runs,
                "retries_used": self.retries_used,
                "stage_plan": tasks_module.STAGE_RUNS[self.stage],
                "retry_budget": RETRY_BUDGET[self.stage],
                "cap": HERCULES_BUDGET_CAP,
            },
            "cells": [{"task_id": cell.task_id, "seed": cell.seed} for cell in plan_cells(self.exp_id, self.stage, tasks=self.tasks, force=True)],
            "metrics": summary.as_dict(),
            "finished_at": _now(),
        }
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("orchestrator: manifest written to %s", self.manifest_path)


def _junit_verdict(result: runner_module.RunResult) -> tuple[bool | None, str | None]:
    """``(junit_passed, junit_terminate)``; both ``None`` when the run produced no usable JUnit artefact."""
    if result.status not in (runner_module.STATUS_PASSED, runner_module.STATUS_FAILED):
        return None, None
    terminate: str | None = None
    if result.junit_xml:
        try:
            terminate = runner_module.parse_junit_xml(Path(result.junit_xml)).get("terminate")
        except runner_module.JUnitParseError:  # pragma: no cover - runner already parsed it once
            terminate = None
    return result.passed, terminate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MiniWoB++ benchmark orchestration (spec §7)")
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--stage", required=True, choices=list(tasks_module.STAGES))
    parser.add_argument("--dry-run", action="store_true", help="print the cell plan and the budget without touching anything")
    parser.add_argument("--force", action="store_true", help="ignore existing results.jsonl rows and re-plan every cell")
    parser.add_argument("--exp-root", default=str(DEFAULT_EXP_ROOT))
    parser.add_argument("--html-root", default=str(MINIWOB_HTML_ROOT))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--episode-ms", type=int, default=tasks_module.EPISODE_MAX_TIME_MS_DEFAULT)
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    try:
        orchestrator = Orchestrator(
            args.exp_id,
            stage=args.stage,
            exp_root=Path(args.exp_root),
            html_root=Path(args.html_root),
            host=args.host,
            port=args.port,
            episode_ms=args.episode_ms,
            timeout_s=args.timeout_s,
            dry_run=args.dry_run,
            force=args.force,
        )
        return orchestrator.run()
    except (BenchmarkError, tasks_module.BenchmarkError, runner_module.RunnerError) as exc:
        # RunnerError = unusable executor environment (missing key file, unusable paths): no traceback
        logger.error("orchestrator: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
