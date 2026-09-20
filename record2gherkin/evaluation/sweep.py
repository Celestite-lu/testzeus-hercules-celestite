"""Experiment orchestration: matrix, seed derivation, budget guard and the sweep CLI (spec §7/§8).

CLI::

    python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage pilot
    python -m record2gherkin.evaluation.sweep --exp-id exp001 --stage full --dry-run

Every cell is ``(method, flow, mutation)``; its M3/M4 seed is derived from the *cell coordinates*
(never from ``run_id``, which embeds the seed — spec §2.3)::

    seed   = int(sha1(f"{exp_id}:{method}:{flow}:{mutation}").hexdigest()[:8], 16)
    run_id = f"{method}__{flow}__{mutation}__s{seed}"

The demo server is a sub-process; each cell sets ``(mutation, seed)`` over ``POST /__control`` and
verifies it over ``GET /healthz`` before running.  Nothing in ``results.jsonl`` / ``manifest.json``
carries the API key (spec §8).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from record2gherkin.evaluation import baseline as baseline_module
from record2gherkin.evaluation import metrics as metrics_module
from record2gherkin.evaluation import runner as runner_module
from record2gherkin.evaluation.demo_app import MUTATIONS
from record2gherkin.evaluation.demo_server import (
    CONTROL_PATH,
    DEFAULT_HOST,
    DEFAULT_PORT,
    HEALTH_PATH,
)
from testzeus_hercules.utils.logger import logger

REPO_ROOT = runner_module.REPO_ROOT
DEFAULT_EXP_ROOT = REPO_ROOT / "dev_runs" / "experiments"
DEFAULT_RECORDINGS_DIR = DEFAULT_EXP_ROOT / "recordings"
DEFAULT_FEATURES_DIR = DEFAULT_EXP_ROOT / "features"

FLOWS: tuple[str, ...] = metrics_module.FLOWS
METHODS: tuple[str, ...] = metrics_module.METHODS
GENERATED, BASELINE = METHODS

#: D2 pilot cells: F3 × {M0, M3} + F2 × M0 (review 必改 4: occurrence 步骤首验) + ≤1 基础设施重试.
PILOT_CELLS: tuple[tuple[str, str], ...] = (("F3", "M0"), ("F3", "M3"), ("F2", "M0"))
#: Stage → Hercules run count used for the budget guard (spec §7).
STAGE_HERCULES_RUNS: Mapping[str, int] = {"pilot": 4, "full": len(FLOWS) * len(MUTATIONS), "baseline": 0}
#: Infrastructure retry buffer: timeout / no_junit / crashed service only (never a failed用例).
INFRA_RETRY_BUDGET = 5
#: Hard red line of the experiment (spec §7: Hercules 合计 ≤39, 护栏红线 ≤40).
HERCULES_BUDGET_CAP = 40
INFRA_STATUSES = frozenset({runner_module.STATUS_TIMEOUT, runner_module.STATUS_NO_JUNIT})


class SweepError(RuntimeError):
    """Orchestration error (busy port, missing features, unreachable server, budget breach)."""


@dataclass(frozen=True)
class Cell:
    """One experiment cell; the seed/run_id are derived, never stored in the spec."""

    method: str
    flow: str
    mutation: str

    @property
    def label(self) -> str:
        return f"{self.method}/{self.flow}/{self.mutation}"


# ---------------------------------------------------------------------------------------------
# 矩阵、seed 派生与预算护栏 (spec §2.3, §7)
# ---------------------------------------------------------------------------------------------


def matrix(*, methods: Sequence[str] = METHODS, flows: Sequence[str] = FLOWS, mutations: Sequence[str] = MUTATIONS) -> tuple[Cell, ...]:
    """The full experiment matrix: 2 methods × 6 flows × 5 mutations = 60 cells."""
    return tuple(Cell(method, flow, mutation) for method in methods for flow in flows for mutation in mutations)


def stage_cells(stage: str, *, flows: Sequence[str] = FLOWS) -> tuple[Cell, ...]:
    """Cells executed by one stage (spec §7)."""
    if stage == "pilot":
        return tuple(Cell(GENERATED, flow, mutation) for flow, mutation in PILOT_CELLS)
    if stage == "full":
        return tuple(Cell(GENERATED, flow, mutation) for flow in flows for mutation in MUTATIONS)
    if stage == "baseline":
        return tuple(Cell(BASELINE, flow, mutation) for flow in flows for mutation in MUTATIONS)
    raise SweepError(f"unknown stage: {stage!r} (expected pilot/full/baseline)")


def derive_seed(exp_id: str, method: str, flow: str, mutation: str) -> int:
    """Deterministic cell seed (spec §2.3); input never contains the seed itself."""
    digest = hashlib.sha1(f"{exp_id}:{method}:{flow}:{mutation}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def make_run_id(method: str, flow: str, mutation: str, seed: int) -> str:
    """``<method>__<flow>__<mutation>__s<seed>`` (spec §4.1); seed must already be final."""
    return f"{method}__{flow}__{mutation}__s{seed}"


def hercules_budget(*stages: str, retry_budget: int = INFRA_RETRY_BUDGET) -> dict[str, int]:
    """Planned Hercules runs for the given stages plus the infrastructure retry buffer."""
    breakdown = {stage: STAGE_HERCULES_RUNS[stage] for stage in stages if stage in STAGE_HERCULES_RUNS}
    total = sum(breakdown.values())
    return {"breakdown": breakdown, "retry": retry_budget, "total": total + retry_budget, "cap": HERCULES_BUDGET_CAP}


def assert_budget(planned_runs: int, *, cap: int = HERCULES_BUDGET_CAP) -> None:
    """Guard rail of spec §7: the experiment must never plan more than ``cap`` Hercules runs."""
    if planned_runs > cap:
        raise SweepError(f"budget breach: {planned_runs} Hercules runs planned, cap is {cap}")


# ---------------------------------------------------------------------------------------------
# Demo server plumbing (spec §3.3)
# ---------------------------------------------------------------------------------------------


def probe_port(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout_s: float = 1.0) -> bool:
    """True when something is already listening on ``host:port`` (spec §3.1: 启动前探测占用)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex((host, port)) == 0


def _http_json(url: str, payload: Mapping[str, Any] | None = None, timeout_s: float = 5.0) -> dict[str, Any]:
    # 显式禁用代理：本机 macOS 的系统代理会让 127.0.0.1 的回环请求走代理并返回 502。
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_s) as response:  # noqa: S310 - loopback only
        return json.loads(response.read().decode("utf-8"))


def get_health(base_url: str) -> dict[str, Any]:
    return _http_json(f"{base_url}{HEALTH_PATH}")


def set_demo_state(base_url: str, mutation: str, seed: int) -> dict[str, Any]:
    """Update the demo server state over ``POST /__control`` and verify it (spec §3.3)."""
    try:
        payload = _http_json(f"{base_url}{CONTROL_PATH}", {"mutation": mutation, "seed": seed})
    except (urllib.error.URLError, ValueError) as exc:
        raise SweepError(f"cannot set demo state {mutation}/{seed} at {base_url}: {exc}") from exc
    acknowledged = _http_json(f"{base_url}{HEALTH_PATH}")
    if acknowledged.get("mutation") != mutation or acknowledged.get("seed") != seed:
        raise SweepError(f"demo state mismatch: wanted {mutation}/{seed}, server reports {acknowledged}")
    return payload


def start_demo_server(port: int = DEFAULT_PORT, *, host: str = DEFAULT_HOST) -> subprocess.Popen[str]:
    """Start the demo server as a sub-process (spec §3.4), after probing the port."""
    if probe_port(host, port):
        raise SweepError(f"port {host}:{port} is already in use; free it before the sweep (URL is an experiment parameter)")
    process = subprocess.Popen(
        [sys.executable, "-m", "record2gherkin.evaluation.demo_server", "--host", host, "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://{host}:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise SweepError(f"demo server exited early (code {process.returncode}): {output[-2000:]}")
        try:
            get_health(base_url)
            logger.info("sweep: demo server up at %s", base_url)
            return process
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.2)
    process.kill()
    raise SweepError("demo server did not become healthy within 15s")


def stop_demo_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()


# ---------------------------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


class Sweep:
    """One exp's orchestration: results.jsonl rows plus the manifest (spec §8)."""

    def __init__(
        self,
        exp_id: str,
        *,
        stage: str,
        exp_root: Path = DEFAULT_EXP_ROOT,
        features_dir: Path = DEFAULT_FEATURES_DIR,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout_s: int = runner_module.DEFAULT_TIMEOUT_S,
        dry_run: bool = False,
    ) -> None:
        self.exp_id = exp_id
        self.stage = stage
        self.exp_dir = Path(exp_root) / exp_id
        self.runs_dir = self.exp_dir / "runs"
        self.results_path = self.exp_dir / "results.jsonl"
        self.manifest_path = self.exp_dir / "manifest.json"
        self.features_dir = Path(features_dir)
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.cells = stage_cells(stage)
        self.started_at = _now()
        self.retry_limit = 1 if stage == "pilot" else (INFRA_RETRY_BUDGET if stage == "full" else 0)
        self.hercules_runs = 0
        self.retries_used = 0

    # -- public entry ------------------------------------------------------------------------

    def run(self) -> int:
        """Execute the stage; returns a process exit code (0 = every cell produced a row)."""
        self._assert_budget()
        if self.dry_run:
            return self._print_plan()

        self.exp_dir.mkdir(parents=True, exist_ok=True)
        server = start_demo_server(self.port, host=self.host)
        try:
            for cell in self.cells:
                self._run_cell(cell)
            self._retry_infrastructure_failures()
        finally:
            stop_demo_server(server)
        self._write_manifest()
        logger.info("sweep: stage %s finished, %s Hercules runs used (cap %s)", self.stage, self.hercules_runs, HERCULES_BUDGET_CAP)
        return 0

    # -- internals ---------------------------------------------------------------------------

    def _assert_budget(self) -> None:
        planned = STAGE_HERCULES_RUNS.get(self.stage, 0)
        assert_budget(planned + INFRA_RETRY_BUDGET)

    def _seed_and_id(self, cell: Cell) -> tuple[int, str]:
        seed = derive_seed(self.exp_id, cell.method, cell.flow, cell.mutation)
        return seed, make_run_id(cell.method, cell.flow, cell.mutation, seed)

    def _print_plan(self) -> int:
        logger.info("sweep: dry-run for exp %s stage %s (%s cells)", self.exp_id, self.stage, len(self.cells))
        for cell in self.cells:
            seed, run_id = self._seed_and_id(cell)
            if cell.method == GENERATED:
                feature = self.features_dir / f"{cell.flow}.feature"
                project_root = self.runs_dir / run_id / "opt"
                cmd = runner_module.build_command(feature, project_root)
                logger.info("sweep: %s seed=%s run_id=%s -> %s", cell.label, seed, run_id, " ".join(cmd))
            else:
                logger.info("sweep: %s seed=%s run_id=%s -> baseline (local playwright)", cell.label, seed, run_id)
        return 0

    def _run_cell(self, cell: Cell, *, attempt: int = 1) -> dict[str, Any]:
        seed, run_id = self._seed_and_id(cell)
        set_demo_state(self.base_url, cell.mutation, seed)
        started_at = _now()
        started = time.monotonic()
        if cell.method == GENERATED:
            row = self._run_generated_cell(cell, run_id=run_id, seed=seed, started_at=started_at)
        else:
            row = self._run_baseline_cell(cell, run_id=run_id, seed=seed, started_at=started_at)
        row["finished_at"] = _now()
        row["attempt"] = attempt
        self._append_row(row)
        logger.info("sweep: %s -> %s (%.1fs)", row["run_id"], row["status"], time.monotonic() - started)
        return row

    def _run_generated_cell(self, cell: Cell, *, run_id: str, seed: int, started_at: str) -> dict[str, Any]:
        if self.hercules_runs >= HERCULES_BUDGET_CAP:
            raise SweepError(f"budget breach: refusing Hercules run {self.hercules_runs + 1} (cap {HERCULES_BUDGET_CAP})")
        feature_source = self.features_dir / f"{cell.flow}.feature"
        if not feature_source.is_file():
            raise SweepError(f"feature not found: {feature_source} (run the recording + distillation step first)")
        run_dir = self.runs_dir / run_id
        project_root = runner_module.prepare_run_dir(run_dir / "opt")
        feature_path = project_root / "input" / feature_source.name
        feature_path.write_text(feature_source.read_text(encoding="utf-8"), encoding="utf-8")
        self.hercules_runs += 1
        result = runner_module.run_feature(feature_path, run_id=run_id, project_root=project_root, timeout_s=self.timeout_s)
        row = runner_module.result_to_row(
            result,
            method=cell.method,
            flow=cell.flow,
            mutation=cell.mutation,
            seed=seed,
            started_at=started_at,
            finished_at=_now(),
            model=runner_module.LLM_MODEL_NAME,
        )
        return row

    def _run_baseline_cell(self, cell: Cell, *, run_id: str, seed: int, started_at: str) -> dict[str, Any]:
        result = baseline_module.run_flow(cell.flow, base_url=self.base_url)
        return runner_module.build_baseline_row(
            run_id=run_id,
            flow=cell.flow,
            mutation=cell.mutation,
            seed=seed,
            passed=result.passed,
            duration_s=result.duration_s,
            failure_message=result.failure_message,
            started_at=started_at,
            finished_at=_now(),
        )

    def _retry_infrastructure_failures(self) -> None:
        """Re-run timeout/no_junit cells while the retry buffer lasts (spec §7: 用例失败不重跑)."""
        if self.retry_limit <= 0:
            return
        rows = metrics_module.load_rows(self.results_path) if self.results_path.is_file() else []
        infra_cells: list[Cell] = []
        for row in rows:
            if row.get("status") not in INFRA_STATUSES:
                continue
            cell = Cell(str(row.get("method")), str(row.get("flow")), str(row.get("mutation")))
            if cell not in infra_cells:
                infra_cells.append(cell)
        for cell in infra_cells:
            if self.retries_used >= self.retry_limit:
                logger.warning("sweep: infrastructure retry buffer exhausted (%s), leaving %s as failed data", self.retry_limit, cell.label)
                break
            self.retries_used += 1
            logger.info("sweep: infrastructure retry %s/%s for %s", self.retries_used, self.retry_limit, cell.label)
            self._run_cell(cell, attempt=self.retries_used + 1)

    def _append_row(self, row: Mapping[str, Any]) -> None:
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_manifest(self) -> None:
        rows = metrics_module.load_rows(self.results_path) if self.results_path.is_file() else []
        summary = metrics_module.summarize(rows, exp_id=self.exp_id)
        manifest = {
            "exp_id": self.exp_id,
            "stage": self.stage,
            "git_rev": git_rev(),
            "started_at": self.started_at,
            "model_name": runner_module.LLM_MODEL_NAME,
            "llm_base_url": runner_module.LLM_MODEL_BASE_URL,
            "demo_port": self.port,
            "features_dir": str(self.features_dir),
            "results_path": str(self.results_path),
            "timeout_s": self.timeout_s,
            "budget": {"hercules_used": self.hercules_runs, "retries_used": self.retries_used, "cap": HERCULES_BUDGET_CAP},
            "cells": [
                {
                    "run_id": make_run_id(cell.method, cell.flow, cell.mutation, derive_seed(self.exp_id, cell.method, cell.flow, cell.mutation)),
                    "method": cell.method,
                    "flow": cell.flow,
                    "mutation": cell.mutation,
                    "seed": derive_seed(self.exp_id, cell.method, cell.flow, cell.mutation),
                }
                for cell in self.cells
            ],
            "metrics": summary.as_dict(),
            "finished_at": _now(),
        }
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("sweep: manifest written to %s", self.manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UI-mutation experiment sweep (spec §7/§8)")
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--stage", required=True, choices=["pilot", "full", "baseline"])
    parser.add_argument("--dry-run", action="store_true", help="build and print the plan without executing anything")
    parser.add_argument("--exp-root", default=str(DEFAULT_EXP_ROOT))
    parser.add_argument("--features-dir", default=str(DEFAULT_FEATURES_DIR))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout-s", type=int, default=runner_module.DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    sweep = Sweep(
        args.exp_id,
        stage=args.stage,
        exp_root=Path(args.exp_root),
        features_dir=Path(args.features_dir),
        host=args.host,
        port=args.port,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
    )
    try:
        return sweep.run()
    except SweepError as exc:
        logger.error("sweep: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
