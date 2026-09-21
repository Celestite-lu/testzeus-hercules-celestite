"""Metric aggregation over ``results.jsonl`` rows (spec §8).

The headline number is the honest one: ``Overall = Σ official_passed / |tasks|`` with **every** task in
the denominator — ``timeout`` / ``no_junit`` / ``no_reward`` / ``no_goal`` / ``official_failed`` rows all
count as 0, and a cell that never produced a row counts as 0 and is listed in ``missing``.  Cost and
duration averages skip ``None`` values and count them separately; ``None`` never silently becomes 0.

Rate metrics use one row per ``(task_id, seed)`` (an infrastructure retry supersedes the earlier row);
cost totals are computed over *all* rows, because every Hercules execution spent tokens and wall clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from record2gherkin.benchmark import tasks as tasks_module

ROW_KEYS: tuple[str, ...] = (
    "run_id",
    "task_id",
    "subdomain",
    "family",
    "visual",
    "seed",
    "goal",
    "episode_max_time_ms",
    "status",
    "official_passed",
    "reward_raw",
    "done",
    "reward_reason",
    "junit_passed",
    "junit_terminate",
    "disagreement",
    "duration_s",
    "total_tokens",
    "cost_usd",
    "junit_xml",
    "failure_message",
    "started_at",
    "finished_at",
    "model",
    # 安全扫描注解（spec §7.6）；不参与任何指标分母，只做逐行披露。
    "task_url_navigations",
    "flagged",
    "invalid_reason",
)

OFFICIAL_PASSED = "official_passed"
OFFICIAL_FAILED = "official_failed"
NON_OFFICIAL_STATUSES: tuple[str, ...] = ("timeout", "no_junit", "no_reward", "no_goal")


@dataclass(frozen=True)
class MetricValue:
    """A rate plus its numerator/denominator and the cells that had to be treated as 0."""

    value: float | None
    passed: int
    total: int
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "passed": self.passed, "total": self.total, "missing": list(self.missing)}


@dataclass(frozen=True)
class CostSummary:
    """Token / duration / cost aggregation over all rows (spec §8.3); ``None`` values are counted, not zeroed."""

    runs: int
    avg_tokens: float | None
    total_tokens: int | None
    tokens_missing_count: int
    avg_duration_s: float | None
    total_duration_s: float | None
    duration_missing_count: int
    avg_cost_usd: float | None
    total_cost_usd: float | None
    cost_missing_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "avg_tokens": self.avg_tokens,
            "total_tokens": self.total_tokens,
            "tokens_missing_count": self.tokens_missing_count,
            "avg_duration_s": self.avg_duration_s,
            "total_duration_s": self.total_duration_s,
            "duration_missing_count": self.duration_missing_count,
            "avg_cost_usd": self.avg_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            "cost_missing_count": self.cost_missing_count,
        }


@dataclass
class Summary:
    """Everything the test report needs, computed once from one exp's rows (spec §8.6)."""

    exp_id: str | None
    rows: int
    cells: int
    duplicate_cells: int
    overall: MetricValue
    families: dict[str, dict[str, Any]] = field(default_factory=dict)
    visual: MetricValue | None = None
    cost: CostSummary | None = None
    junit: MetricValue | None = None
    junit_unavailable_count: int = 0
    disagreement_count: int = 0
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exp_id": self.exp_id,
            "rows": self.rows,
            "cells": self.cells,
            "duplicate_cells": self.duplicate_cells,
            "overall": self.overall.as_dict(),
            "families": self.families,
            "visual": self.visual.as_dict() if self.visual else None,
            "cost": self.cost.as_dict() if self.cost else None,
            "junit": self.junit.as_dict() if self.junit else None,
            "junit_unavailable_count": self.junit_unavailable_count,
            "disagreement_count": self.disagreement_count,
            "disagreements": self.disagreements,
            "status_counts": self.status_counts,
            "failures": self.failures,
        }


# ---------------------------------------------------------------------------------------------
# 载入与归约
# ---------------------------------------------------------------------------------------------


def _library_tasks() -> list[dict[str, Any]]:
    return tasks_module.load_tasks()


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read ``results.jsonl``; blank/garbage lines are skipped (a torn tail must not kill the report)."""
    rows: list[dict[str, Any]] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(payload, Mapping):
            rows.append(dict(payload))
    return rows


def cell_key(row: Mapping[str, Any]) -> tuple[str, int | str]:
    seed = row.get("seed")
    return str(row.get("task_id")), seed if isinstance(seed, int) else str(seed)


def latest_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[tuple[str, int | str], Mapping[str, Any]], int]:
    """One row per cell, last wins (a retry supersedes the earlier failure); returns duplicates count."""
    cells: dict[tuple[str, int | str], Mapping[str, Any]] = {}
    duplicates = 0
    for row in rows:
        key = cell_key(row)
        if key in cells:
            duplicates += 1
        cells[key] = row
    return cells, duplicates


def _passed(row: Mapping[str, Any] | None) -> int:
    if row is None:
        return 0
    return 1 if row.get("official_passed") is True else 0


def index_by_task(cells: Mapping[tuple[str, int | str], Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """``task_id -> row`` (last wins) so one stage never counts the same task twice."""
    indexed: dict[str, Mapping[str, Any]] = {}
    for (task_id, _seed), row in cells.items():
        indexed[task_id] = row
    return indexed


def _rate(cells: Mapping[tuple[str, int | str], Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]]) -> MetricValue:
    """``Σ official_passed / |tasks|``; a cell without a row counts as 0 and is listed as missing."""
    total = len(tasks)
    if total == 0:
        return MetricValue(value=None, passed=0, total=0, missing=())
    indexed = index_by_task(cells)
    passed = 0
    missing: list[str] = []
    for task in tasks:
        task_id = str(task.get("task_id"))
        row = indexed.get(task_id)
        if row is None:
            missing.append(task_id)
        passed += _passed(row)
    return MetricValue(value=passed / total, passed=passed, total=total, missing=tuple(missing))


def family_rates(cells: Mapping[tuple[str, int | str], Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-family rates (spec §8.2); families are reporting buckets only, never a semantic claim."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        grouped.setdefault(str(task.get("family", "")), []).append(dict(task))
    return {family: _rate(cells, group).as_dict() for family, group in sorted(grouped.items())}


def visual_group(cells: Mapping[tuple[str, int | str], Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]]) -> MetricValue:
    """The ``visual-*`` group, disclosed as its own bucket — never exempted (spec §8.2/§11)."""
    visual = [task for task in tasks if task.get("visual") is True]
    return _rate(cells, visual)


def cost_summary(rows: Iterable[Mapping[str, Any]]) -> CostSummary:
    """Token/duration/cost aggregation; the missing counters never hide ``None`` behind a 0 (spec §8.3)."""
    tokens: list[int] = []
    durations: list[float] = []
    costs: list[float] = []
    runs = 0
    for row in rows:
        runs += 1
        token_value = _as_int(row.get("total_tokens"))
        if token_value is not None:
            tokens.append(token_value)
        duration = _as_float(row.get("duration_s"))
        if duration is not None:
            durations.append(duration)
        cost = _as_float(row.get("cost_usd"))
        if cost is not None:
            costs.append(cost)
    return CostSummary(
        runs=runs,
        avg_tokens=(sum(tokens) / len(tokens)) if tokens else None,
        total_tokens=sum(tokens) if tokens else None,
        tokens_missing_count=runs - len(tokens),
        avg_duration_s=(sum(durations) / len(durations)) if durations else None,
        total_duration_s=sum(durations) if durations else None,
        duration_missing_count=runs - len(durations),
        avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
        total_cost_usd=sum(costs) if costs else None,
        cost_missing_count=runs - len(costs),
    )


def disagreements(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows where the planner's JUnit verdict and the official reward disagree (spec §8.4)."""
    found: list[dict[str, Any]] = []
    for row in rows:
        if row.get("disagreement") is not True:
            continue
        found.append(
            {
                "run_id": row.get("run_id"),
                "task_id": row.get("task_id"),
                "junit_passed": row.get("junit_passed"),
                "official_passed": row.get("official_passed"),
            }
        )
    return found


def junit_rate(cells: Mapping[tuple[str, int | str], Mapping[str, Any]], tasks: Sequence[Mapping[str, Any]]) -> tuple[MetricValue, int]:
    """JUnit-side pass rate (transparency only, never the headline) plus its unavailable counter."""
    total = len(tasks)
    if total == 0:
        return MetricValue(value=None, passed=0, total=0, missing=()), 0
    passed = 0
    unavailable = 0
    indexed = index_by_task(cells)
    for task in tasks:
        task_id = str(task.get("task_id"))
        row = indexed.get(task_id)
        if row is None:
            unavailable += 1
            continue
        if row.get("junit_passed") is True:
            passed += 1
        elif row.get("junit_passed") is None:
            unavailable += 1
    return MetricValue(value=passed / total, passed=passed, total=total, missing=()), unavailable


def status_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def failures(rows: Iterable[Mapping[str, Any]], *, limit: int = 240) -> list[dict[str, Any]]:
    """Every non-passing row, ready for the report's failure list (spec §10.7)."""
    listed: list[dict[str, Any]] = []
    for row in rows:
        if row.get("official_passed") is True:
            continue
        listed.append(
            {
                "run_id": row.get("run_id"),
                "task_id": row.get("task_id"),
                "status": row.get("status"),
                "reward_raw": row.get("reward_raw"),
                "junit_passed": row.get("junit_passed"),
                "failure_message": _summarize(row.get("failure_message"), limit=limit),
            }
        )
    return listed


def summarize(rows: Sequence[Mapping[str, Any]], *, tasks: Sequence[Mapping[str, Any]] | None = None, exp_id: str | None = None) -> Summary:
    """Compute the whole metric set for one exp's rows (spec §8)."""
    task_table = [dict(task) for task in tasks] if tasks is not None else _library_tasks()
    cells, duplicates = latest_rows(rows)
    junit_value, junit_unavailable = junit_rate(cells, task_table)
    found = disagreements(rows)
    return Summary(
        exp_id=exp_id,
        rows=len(rows),
        cells=len(cells),
        duplicate_cells=duplicates,
        overall=_rate(cells, task_table),
        families=family_rates(cells, task_table),
        visual=visual_group(cells, task_table),
        cost=cost_summary(rows),
        junit=junit_value,
        junit_unavailable_count=junit_unavailable,
        disagreement_count=len(found),
        disagreements=found,
        status_counts=status_counts(rows),
        failures=failures(rows),
    )


def _summarize(message: Any, *, limit: int = 240) -> str | None:
    if message is None:
        return None
    text = " ".join(str(message).split())
    return text[:limit]


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
