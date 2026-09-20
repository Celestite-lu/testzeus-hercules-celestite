"""Metric aggregation over ``results.jsonl`` rows (spec §6).

Pure functions: the input is the list of row mappings written by the sweep, the output is the three
report tables (first-pass rate, per-mutation survival, cost) plus the failure list.

Formulas (spec §6.1-§6.3, |F| = 6 flows)::

    FirstPass                = Σ_i pass(i, M0, generated) / |F|
    Surv(method, M)          = Σ_i pass(i, M, method) / |F|
    主指标存活率               = mean(Surv(method, M1), Surv(method, M2), Surv(method, M3))   # M4 单列
    AvgCost / TotalCost      = mean / sum of non-None cost_usd, plus cost_missing_count

``pass`` is 0 for every non-passing row (assertion failure, ``timeout``, ``no_junit``): failed runs
stay in the denominator and are reported as failures.  Cells that never produced a row count as 0
and are listed as missing, so a half-finished sweep cannot inflate a rate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

FLOWS: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6")
MUTATIONS: tuple[str, ...] = ("M0", "M1", "M2", "M3", "M4")
METHODS: tuple[str, ...] = ("generated", "baseline")
GENERATED, BASELINE = METHODS
#: 主指标存活率 uses the three "structure" mutations; M4 is reported as its own column (spec §6.2).
HEADLINE_MUTATIONS: tuple[str, ...] = ("M1", "M2", "M3")

ROW_KEYS: tuple[str, ...] = (
    "run_id",
    "method",
    "flow",
    "mutation",
    "seed",
    "status",
    "passed",
    "duration_s",
    "cost_usd",
    "total_tokens",
    "junit_xml",
    "failure_message",
    "started_at",
    "finished_at",
    "model",
)


@dataclass(frozen=True)
class MetricValue:
    """A rate with its numerator/denominator, plus the cells that had to be treated as 0."""

    value: float | None
    passed: int
    total: int
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "passed": self.passed, "total": self.total, "missing": list(self.missing)}


@dataclass(frozen=True)
class CostSummary:
    """Cost/token aggregation (spec §6.3); ``None`` values never silently become 0."""

    avg_cost_usd: float | None
    total_cost_usd: float | None
    cost_missing_count: int
    runs_with_cost: int
    total_tokens: int | None
    runs_with_tokens: int
    tokens_missing_count: int
    hercules_runs: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "avg_cost_usd": self.avg_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            "cost_missing_count": self.cost_missing_count,
            "runs_with_cost": self.runs_with_cost,
            "total_tokens": self.total_tokens,
            "runs_with_tokens": self.runs_with_tokens,
            "tokens_missing_count": self.tokens_missing_count,
            "hercules_runs": self.hercules_runs,
        }


@dataclass
class Summary:
    """Everything the report needs, computed once from one exp's rows (spec §10.7)."""

    exp_id: str | None
    rows: int
    cells: int
    duplicate_cells: int
    first_pass: MetricValue | None
    survival: dict[str, dict[str, MetricValue]] = field(default_factory=dict)
    headline_survival: dict[str, float | None] = field(default_factory=dict)
    cost: CostSummary | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exp_id": self.exp_id,
            "rows": self.rows,
            "cells": self.cells,
            "duplicate_cells": self.duplicate_cells,
            "first_pass": self.first_pass.as_dict() if self.first_pass else None,
            "survival": {method: {mutation: value.as_dict() for mutation, value in group.items()} for method, group in self.survival.items()},
            "headline_survival": self.headline_survival,
            "cost": self.cost.as_dict() if self.cost else None,
            "failures": self.failures,
        }


# ---------------------------------------------------------------------------------------------
# 载入与归约
# ---------------------------------------------------------------------------------------------


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


def cell_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(row.get("method")), str(row.get("flow")), str(row.get("mutation"))


def latest_cell_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[tuple[str, str, str], Mapping[str, Any]], int]:
    """Reduce rows to one row per cell; the last row wins (infrastructure retries supersede).

    Returns ``(cell -> row, duplicate_row_count)``.
    """
    cells: dict[tuple[str, str, str], Mapping[str, Any]] = {}
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
    return 1 if row.get("passed") is True else 0


def rate(cells: Mapping[tuple[str, str, str], Mapping[str, Any]], *, method: str, mutation: str, flows: Sequence[str]) -> MetricValue:
    """``Surv(method, mutation)``; cells without a row are counted as 0 and reported as missing."""
    denominator = len(flows)
    if denominator == 0:
        return MetricValue(value=None, passed=0, total=0, missing=())
    passed = 0
    missing: list[str] = []
    for flow in flows:
        row = cells.get((method, flow, mutation))
        if row is None:
            missing.append(flow)
        passed += _passed(row)
    return MetricValue(value=passed / denominator, passed=passed, total=denominator, missing=tuple(missing))


def first_pass(cells: Mapping[tuple[str, str, str], Mapping[str, Any]], *, flows: Sequence[str] = FLOWS) -> MetricValue:
    """首轮通过率 (spec §6.1): generated × M0, one row per flow, pilot rows excluded by the caller."""
    return rate(cells, method=GENERATED, mutation="M0", flows=flows)


def survival_table(cells: Mapping[tuple[str, str, str], Mapping[str, Any]], *, methods: Sequence[str] = METHODS, flows: Sequence[str] = FLOWS) -> dict[str, dict[str, MetricValue]]:
    """Surv(method, M) for the full method × mutation grid (spec §6.2)."""
    table: dict[str, dict[str, MetricValue]] = {}
    for method in methods:
        table[method] = {}
        for mutation in MUTATIONS:
            table[method][mutation] = rate(cells, method=method, mutation=mutation, flows=flows)
    return table


def headline_survival(table: Mapping[str, Mapping[str, MetricValue]], *, mutations: Sequence[str] = HEADLINE_MUTATIONS) -> dict[str, float | None]:
    """主指标存活率: mean of the M1/M2/M3 columns per method (M4 stays out, spec §6.2)."""
    headline: dict[str, float | None] = {}
    for method, group in table.items():
        values = [group[mutation].value for mutation in mutations if mutation in group and group[mutation].value is not None]
        headline[method] = sum(values) / len(values) if values else None
    return headline


def cost_summary(rows: Iterable[Mapping[str, Any]]) -> CostSummary:
    """Cost/token aggregation over generated rows (spec §6.3); baseline rows have no cost by design."""
    costs: list[float] = []
    tokens: list[int] = []
    hercules_runs = 0
    for row in rows:
        if str(row.get("method")) != GENERATED:
            continue
        hercules_runs += 1
        cost = _as_float(row.get("cost_usd"))
        if cost is not None:
            costs.append(cost)
        token_value = _as_int(row.get("total_tokens"))
        if token_value is not None:
            tokens.append(token_value)
    return CostSummary(
        avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
        total_cost_usd=sum(costs) if costs else None,
        cost_missing_count=hercules_runs - len(costs),
        runs_with_cost=len(costs),
        total_tokens=sum(tokens) if tokens else None,
        runs_with_tokens=len(tokens),
        tokens_missing_count=hercules_runs - len(tokens),
        hercules_runs=hercules_runs,
    )


def failure_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Failed runs (assertion failure / timeout / no_junit), ready for the report's failure list."""
    failures: list[dict[str, Any]] = []
    for row in rows:
        if row.get("passed") is True:
            continue
        failures.append(
            {
                "run_id": row.get("run_id"),
                "method": row.get("method"),
                "flow": row.get("flow"),
                "mutation": row.get("mutation"),
                "status": row.get("status"),
                "failure_message": _summarize(row.get("failure_message")),
            }
        )
    return failures


def summarize(rows: Sequence[Mapping[str, Any]], *, exp_id: str | None = None, flows: Sequence[str] = FLOWS) -> Summary:
    """Compute the whole metric set for one exp's rows (spec §10.7)."""
    cells, duplicates = latest_cell_rows(rows)
    table = survival_table(cells, flows=flows)
    return Summary(
        exp_id=exp_id,
        rows=len(rows),
        cells=len(cells),
        duplicate_cells=duplicates,
        first_pass=first_pass(cells, flows=flows),
        survival=table,
        headline_survival=headline_survival(table),
        cost=cost_summary(rows),
        failures=failure_rows(rows),
    )


def _summarize(message: Any, limit: int = 240) -> str | None:
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
        return int(value)
    except (TypeError, ValueError):
        return None
