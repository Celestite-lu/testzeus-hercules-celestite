"""Benchmark orchestration (spec §7, r2 increments in spec-r2).

CLI::

    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-pilot --stage pilot
    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full
    uv run python -m record2gherkin.benchmark.orchestrator --exp-id miniwob-full --stage full --dry-run

Per cell the harness derives the seed, pre-reads the goal in its own browser, renders the feature,
runs Hercules in a sub-process (:mod:`record2gherkin.evaluation.runner`) and finally asks the patch
server for the terminal reward — the **only** authority for ``official_passed`` (spec §0 口径 4,
restored by the always-on C1a fix of spec-r2 §1.1: a positive page reward outranks engine
infra statuses).  Every cell produces one appended ``results.jsonl`` row per attempt (r2: each
attempt logs to its own ``runs/<run_id>/attempt<N>/stdout.log``); ``timeout`` / ``no_junit`` /
``no_reward`` cells may be retried once each (infrastructure only, circuit-broken rows excluded), a
failed episode never is.

Every executed cell is additionally scanned for integrity (安全审查 R1 §4.2/§4.3, spec §7.6) over the
artefacts that are already on disk: the renavigation count, ``file://`` escapes and sandbox-tool calls
from the attempt's ``stdout.log`` (H2) plus the ``rewards.jsonl`` record count / ``reason`` domain of
the cell (H3).  The scan only annotates the row (``task_url_navigations`` / ``flagged`` /
``invalid_reason``) — it never rewrites ``status`` or ``official_passed``.

r2 operations (all flag-gated, default off; C1a/C1b/C1c/C1d are always on):

* C1c balance preflight — non-dry-run stages probe the LLM (same ChatOpenAI transport as the engine)
  *before* the server starts; failure exits 3 without executing anything.
* C1b circuit breaker — a timeout/no_junit attempt that died fast with a 402/balance/connection
  marker in its log is excluded from the retry pool; two consecutive such rows abort the stage.
* ``--terminal-cue`` / ``--single-start`` — miniwob_server patch layers (spec-r2 §4).
* ``--role-routing`` / ``--nav-model`` — per-role model routing; generates
  ``<exp_dir>/agents_llm_config.json`` (never containing the key) and injects the four env keys.
* ``--latency-env`` / ``--extra-tools`` / ``--template-notes`` / ``--smoke-cells`` — engine env
  pack, extra tool gate, Gherkin template notes and the pilot drag-smoke appendix.
  All switches are recorded in the manifest ``flags`` object.
* ``--provider {deepseek,glm}`` (r2) — LLM provider of the experiment (default ``deepseek`` keeps
  the r1 status quo byte-for-byte).  ``glm`` reads ``GLM-Key.txt`` (KV format, key travels through
  env only), rides the coding-plan endpoint with ``glm-5.3-flash`` and defaults the routed nav
  model to ``glm-5.3-flash`` / the planner to ``glm-5.3``; a non-default provider adds
  ``llm_provider`` + ``model`` to the manifest for 口径披露.

r3 operations (spec-r3 §7, all flag-gated, default off = r2 behaviour):

* ``--nav-max-tokens`` (R3-1) — ``NAV_MAX_COMPLETION_TOKENS`` completion cap for the nav/executor
  chat models; the headline tightens the adapt-injected 4096 to 768.
* ``--planner-timeout`` (R3-2) — ``LLM_PLANNER_REQUEST_TIMEOUT``, same-sourced into the planner's
  graph-level ``wait_for`` *and* its provider ``timeout``; other agents keep ``LLM_REQUEST_TIMEOUT``.
* ``--extra-tools-modules`` (E1) — extra_tools subset allowlist (default ``drag_and_drop_tool`` only;
  ``all`` = the full r2 load, disclosed as ``["__all__"]`` in ``flags``).
* ``--disable-sandbox`` (E5) — ``SANDBOX_DISABLED=true``; a blocked sandbox attempt is still scanned
  as ``sandbox_tool_invoked``.
* ``--assert-discipline`` (R3-6/F) — ``PLANNER_ASSERT_DISCIPLINE=true`` planner prompt preamble.

Flag-less harness disclosures (spec-r3 §5, judgement-neutral): file-tool call markers invalidate the
cell (E2), the ``open_url`` scheme whitelist logs ``[OPEN_URL_BLOCKED]`` flagged-only (E3), the
metrics ``clean`` calibre + ``invalid_cells`` list (E4), and the ``goal_read_error.log`` trace for
extreme-early-crash cells (E6).
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
from record2gherkin.benchmark import preflight as preflight_module
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
#: spec-r2 §0.2: the r1 cap of 142 is refined to 144 — smoke 14 (pilot 10 + retries 2 + drag smoke 2)
#: plus headline 130 stays inside it; the ablation runs live outside this cap in their own exp roots.
R2_BUDGET_CAP = 144
#: spec §7.5: infrastructure retry buffers (pilot 10+2, full 125+5).  Smoke cells never retry.
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

#: spec-r2 §2.2 (C1b): markers that turn a fast infra death into a circuit-break event.
#: 2026-09-22 增补限流族（security-review-r2-pre + coding plan 5h 窗口）："Error code: 429" 是
#: langchain 的标准报错形态；裸 "429" 会误命中时长数字，不用。
CIRCUIT_BREAK_MARKERS = (
    "402",
    "Insufficient Balance",
    "insufficient balance",
    "insufficient_user_balance",
    "insufficient_quota",
    "Error code: 429",
    "Too Many Requests",
    "RateLimitError",
    "rate limit",
    "限流",
    "Connection error",
    "APIConnectionError",
)
CIRCUIT_BREAK_DURATION_S = 30.0
CIRCUIT_BREAK_RUN_LIMIT = 2  # consecutive breaker rows -> abort the whole stage (exit 2)

#: spec-r2 §5.3 (C4f ``--latency-env``): the whole latency pack travels as one env block; the three
#: engine keys can still be overridden per key on the engine side.
LATENCY_ENV_OVERRIDES: Mapping[str, str] = {
    "LLM_REQUEST_TIMEOUT": "90",  # default 60 (llm_helper.py:21)
    "LLM_MAX_RETRIES": "2",  # default 1 (llm_helper.py:22)
    "BROWSER_STATE_REFRESH_MODE": "markers_only",
    "BROWSER_NAV_MAX_CHAT_ROUND": "30",
    "NAV_STEP_TIME_BUDGET_S": "120",
}

#: spec-r2 §6 (C5 ``--role-routing``): the generated per-role config and its child-env transport.
ROLE_ROUTING_REF_KEY = "litellm"
#: Planner/helper stay on the r1 benchmark model; only the nav (executor) role is routed (plan-r2 C5).
ROLE_ROUTING_PLANNER_MODEL = runner_module.LLM_MODEL_NAME
ROLE_ROUTING_HELPER_MODEL = runner_module.LLM_MODEL_NAME
DEFAULT_NAV_MODEL = "deepseek-flash"
#: r2 ``--provider glm``: the GLM coding-plan routing defaults — nav rides the flash tier (the
#: provider's own model) while the planner keeps the flagship model.
GLM_PLANNER_MODEL = "glm-5.3"
AGENTS_LLM_CONFIG_FILENAME = "agents_llm_config.json"

# -- r3 switch set (spec-r3 §7) -----------------------------------------------------------------
#: spec-r3 §5.1 (E1): default extra_tools subset — drag only; ``all`` restores the full r2 load.
DEFAULT_EXTRA_TOOLS_MODULES = "drag_and_drop_tool"
#: manifest marker for the "no allowlist" (full r2 extra_tools load) choice.
EXTRA_TOOLS_MODULES_ALL = "__all__"


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


def hercules_budget(*stages: str, extra_runs: int = 0) -> dict[str, Any]:
    """Planned Hercules runs per stage plus the infrastructure retry buffer (spec §7.5, spec-r2 §0.2).

    ``extra_runs`` counts appended ``--smoke-cells`` executions (spec-r2 §4.1); they are never
    retried, so they enter the total but not the retry budget.
    """
    breakdown: dict[str, int] = {}
    retry = 0
    for stage in stages:
        if stage not in tasks_module.STAGE_RUNS:
            raise BenchmarkError(f"unknown stage: {stage!r} (expected one of {tasks_module.STAGES})")
        breakdown[stage] = tasks_module.STAGE_RUNS[stage]
        retry += RETRY_BUDGET[stage]
    if extra_runs:
        breakdown["smoke_cells"] = int(extra_runs)
    total = sum(breakdown.values()) + retry
    return {"breakdown": breakdown, "retry": retry, "total": total, "cap": R2_BUDGET_CAP}


def assert_budget(planned_runs: int, *, cap: int = R2_BUDGET_CAP) -> None:
    """Guard rail of spec §7.5 / spec-r2 §0.2: never plan more than ``cap`` Hercules runs."""
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
    task_url_navigations: int = 0,
    flagged: bool = False,
    invalid_reason: str | None = None,
    attempt: int = 1,
    infra_circuit_break: bool = False,
) -> dict[str, Any]:
    """Assemble one ``results.jsonl`` row (spec §7.1) with the r2 status rules of spec-r2 §1.1.

    ``runner_status=None`` means "not executed" (goal pre-read failed, §7.2.5) and keeps the highest
    priority.  C1a (always on, a correctness fix — not a relaxation): a positive page reward inside
    the window outranks engine infra statuses, so "engine timed out but the page already passed"
    cells are officially passed; a negative/zero page reward keeps the infra status (still retryable)
    or falls through to the official verdict.  ``fetch_reward`` stays ``/latest`` (last-wins) — no
    new whitewash channel.

    The row additionally discloses ``runner_status`` — the status the row would have carried under
    the r1 priority chain (``None`` for no_goal) so rescue cells stay auditable — plus ``attempt``
    and ``infra_circuit_break`` (spec-r2 §1.2).  These keys, like the security-scan annotations of
    spec §7.6, never change any metric denominator.
    """
    raw = _reward_raw_value(reward)
    if runner_status is None:
        status = STATUS_NO_GOAL
    elif raw is not None and raw > 0:
        status = STATUS_OFFICIAL_PASSED
    elif runner_status in (runner_module.STATUS_TIMEOUT, runner_module.STATUS_NO_JUNIT):
        status = runner_status
    elif reward is None:
        status = STATUS_NO_REWARD
    else:
        status = STATUS_OFFICIAL_FAILED

    if runner_status is None:
        runner_status_disclosure: str | None = None
    elif runner_status in (runner_module.STATUS_TIMEOUT, runner_module.STATUS_NO_JUNIT):
        runner_status_disclosure = runner_status
    elif reward is None:
        runner_status_disclosure = STATUS_NO_REWARD
    else:
        runner_status_disclosure = STATUS_OFFICIAL_PASSED if (raw is not None and raw > 0) else STATUS_OFFICIAL_FAILED

    official_passed = status == STATUS_OFFICIAL_PASSED
    if status in (STATUS_OFFICIAL_PASSED, STATUS_OFFICIAL_FAILED):
        disagreement: bool | None = junit_passed is not None and junit_passed != official_passed
    else:
        disagreement = None

    subdomain = str(task["subdomain"])
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
        "runner_status": runner_status_disclosure,
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
        "task_url_navigations": int(task_url_navigations),
        "flagged": bool(flagged),
        "invalid_reason": invalid_reason,
        "attempt": int(attempt),
        "infra_circuit_break": bool(infra_circuit_break),
    }


# ---------------------------------------------------------------------------------------------
# Cell 收尾完整性扫描（安全审查 R1 §4.2/§4.3，H2 + H3）
# ---------------------------------------------------------------------------------------------

#: ``open_url`` 的导航日志行标记（V4 计数依据：``Opening URL: <url> (force_new_tab=...)``）。
TASK_URL_NAVIGATION_MARKER = "Opening URL:"
#: 同一 cell 的任务 URL 被打开超过这个次数即 flagged（V4：240s 计时重置 / 失败提交可被重开洗白）。
TASK_URL_NAVIGATION_FLAG_THRESHOLD = 1
#: V5：本地文件逃逸；命中即 cell 无效（安全事件）。
FILE_URL_MARKER = "file://"
#: V6：Python 沙箱**被调用**的日志标记（``execute_python_sandbox.py:62,70``）。绝不能匹配工具注册
#: 日志（``[TOOL_DEBUG] ... 'execute_python_sandbox'`` / ``Registered tool: execute_python_sandbox``
#: 在每个 run 里都出现），否则每个 cell 都会被误判。
#: spec-r3 §5.5 (E5)：``[SANDBOX_DISABLED]`` = 被关停机制拦截的尝试 —— 契约不变：尝试即无效。
SANDBOX_CALL_MARKERS = ("Executing Python sandbox:", "Using sandbox tenant", "[SANDBOX_DISABLED]")
#: V3：合法 ``reason`` 域 = ``{"", "timed out"}``（审查报告 §2.V3）+ 复核 vendored 树后补充的
#: ``unicode-test`` 两个终局原因（只有 ``core/core.js:102`` 与 ``miniwob/unicode-test.html:53,55``
#: 传第三个参数；其余 129 个任务页都不传）。
LEGAL_REWARD_REASONS = frozenset({"", "timed out", "Cool!"})
LEGAL_REWARD_REASON_PREFIXES = ("You clicked on ",)

#: ``invalid_reason`` 取值（非 null ⇒ 该 cell 判为无效，安全事件）。
INVALID_REASON_FILE_URL = "file_url_navigation"
INVALID_REASON_SANDBOX = "sandbox_tool_invoked"
#: spec-r3 §5.2 (E2)：文件工具**调用**日志行标记（file_handler_tool.py 三函数入口）——命中即无效，
#: 与 r2 的 file_url/sandbox 同路；九格静默成功的检测缺口就此封死。
FILE_TOOL_CALL_MARKERS = (
    "[EXTRA_TOOL_CALL] persist_findings",
    "[EXTRA_TOOL_CALL] recall_findings",
    "[EXTRA_TOOL_CALL] augment_findings",
)
INVALID_REASON_FILE_TOOL = "file_tool_invoked"
#: spec-r3 §5.3 (E3)：open_url scheme 白名单的拒绝日志标记 —— **仅披露**（flagged=True、不改
#: invalid_reason），与 r2 对 javascript: 尝试"低危披露不判无效"的处理对齐。
OPEN_URL_BLOCKED_MARKER = "[OPEN_URL_BLOCKED]"


def _attempt_circuit_break(result: runner_module.RunResult, *, stdout_log_path: str | Path) -> bool:
    """C1b attempt-level breaker (spec-r2 §2.2): a fast infra death carrying a 402/connection marker.

    All three conditions must hold: the runner ended in ``timeout``/``no_junit``, the attempt died in
    under :data:`CIRCUIT_BREAK_DURATION_S`, and the attempt's own ``stdout.log`` contains any of
    :data:`CIRCUIT_BREAK_MARKERS`.  A breaker row keeps its §1.1 status untouched — it is only kept
    out of the retry pool so a dead key cannot burn the retry budget.
    """
    if result.status not in (runner_module.STATUS_TIMEOUT, runner_module.STATUS_NO_JUNIT):
        return False
    if result.duration_s is None or result.duration_s >= CIRCUIT_BREAK_DURATION_S:
        return False
    try:
        text = Path(stdout_log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in CIRCUIT_BREAK_MARKERS)


@dataclass(frozen=True)
class CellScan:
    """One cell's post-run integrity scan: the three §7.1 annotation keys (H2 stdout.log + H3 rewards).

    ``flagged`` is the disclosure bit: it is set for the V4 renavigation count, for the V5/V6 security
    events **and** for the V3 reward anomalies.  ``invalid_reason`` is only set by the V5/V6 security
    events — a ``flagged`` row with ``invalid_reason=None`` is "disclose, don't re-judge" (spec §7.6).
    """

    task_url_navigations: int = 0
    reward_records: int = 0
    flagged: bool = False
    invalid_reason: str | None = None

    def merge(self, other: "CellScan") -> "CellScan":
        """Conjunction of two scans (each field has exactly one producer, merging is fieldwise)."""
        reasons = sorted({reason for reason in (self.invalid_reason, other.invalid_reason) if reason})
        return CellScan(
            task_url_navigations=max(self.task_url_navigations, other.task_url_navigations),
            reward_records=max(self.reward_records, other.reward_records),
            flagged=self.flagged or other.flagged,
            invalid_reason="; ".join(reasons) or None,
        )


def _page_signature(subdomain: str, seed: int) -> tuple[str, str]:
    """Two substrings that both have to appear in a log line for it to be *this* cell's page."""
    return f"miniwob/{subdomain}.html", f"r2g_seed={seed}"


def scan_cell_log(log_path: str | Path, *, subdomain: str, seed: int) -> CellScan:
    """H2 — read the already-written ``stdout.log`` and do four things (安全审查 R1 §4.2).

    ① count the navigations to this cell's seeded task URL (``task_url_navigations``); more than one
    flags the row (V4: every renavigation re-runs patch A, resetting the 240s clock and discarding the
    previous failed submission);
    ② any ``file://`` in the log marks the cell invalid + security event (V5: local files — the API key
    file included — can reach the LLM context through ``get_page_text``);
    ③ any *call* marker of the Python sandbox tool does the same (V6: the restricted tenant is not a
    security boundary — ``open()``, ``os.environ`` and ``page.evaluate`` are all reachable; the r3
    ``[SANDBOX_DISABLED]`` refusal marker counts too — a blocked attempt is still an attempt);
    ④ file-tool call markers (``[EXTRA_TOOL_CALL] persist/recall/augment_findings``, spec-r3 §5.2)
    mark the cell invalid (``file_tool_invoked``) — the r2 silent-success detection gap.

    Additionally, the ``[OPEN_URL_BLOCKED]`` scheme-whitelist refusal (spec-r3 §5.3) sets
    ``flagged`` **only** (disclosure, never ``invalid_reason``), matching the r2 treatment of the
    low-risk ``javascript:`` attempts.

    A missing/unreadable log yields a neutral scan (0 navigations, no marker): no evidence, no claim.
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return CellScan()
    subdomain_marker, seed_marker = _page_signature(subdomain, seed)
    navigations = 0
    for line in text.splitlines():
        if TASK_URL_NAVIGATION_MARKER in line and subdomain_marker in line and seed_marker in line:
            navigations += 1

    reasons: list[str] = []
    if FILE_URL_MARKER in text:
        reasons.append(INVALID_REASON_FILE_URL)
    if any(marker in text for marker in SANDBOX_CALL_MARKERS):
        reasons.append(INVALID_REASON_SANDBOX)
    if any(marker in text for marker in FILE_TOOL_CALL_MARKERS):
        reasons.append(INVALID_REASON_FILE_TOOL)
    return CellScan(
        task_url_navigations=navigations,
        flagged=navigations > TASK_URL_NAVIGATION_FLAG_THRESHOLD or bool(reasons) or OPEN_URL_BLOCKED_MARKER in text,
        invalid_reason="; ".join(reasons) or None,
    )


def _reward_lines(rewards_path: str | Path) -> list[dict[str, Any]]:
    """Tolerant ``rewards.jsonl`` read (a torn tail must not kill the scan; the server flushes per line)."""
    try:
        text = Path(rewards_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(payload, Mapping):
            lines.append(dict(payload))
    return lines


def _in_window(record: Mapping[str, Any], started_at: str | None, finished_at: str | None) -> bool:
    """True when the server-received timestamp falls inside this attempt's window.

    A cell that is retried reuses the same ``(path, seed)``, so the file holds the previous attempt's
    records too; only the ones received during this attempt may be compared against this attempt's page
    loads.  Records without a parseable ``received_at`` are counted (safer side).
    """
    received = record.get("received_at")
    if not isinstance(received, str) or not received.strip():
        return True
    received = received.strip()
    if isinstance(started_at, str) and started_at and received < started_at:
        return False
    if isinstance(finished_at, str) and finished_at and received > finished_at:
        return False
    return True


def _matches_cell(record: Mapping[str, Any], *, subdomain: str, seed: int) -> bool:
    """Same matching rule as the server's ``/latest``: path basename + seed (spec §3.3)."""
    path = record.get("path")
    if not isinstance(path, str) or path.rsplit("/", 1)[-1] != f"{subdomain}.html":
        return False
    return str(record.get("seed")) == str(seed)


def scan_cell_rewards(
    rewards_path: str | Path,
    *,
    subdomain: str,
    seed: int,
    page_loads: int,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> CellScan:
    """H3 — check this cell's ``(path, seed)`` records in ``rewards.jsonl`` (安全审查 R1 §4.3, V3).

    Three anomalies flag the row (never a re-judgement, spec §0 口径 4): the record count exceeds the
    page loads of this attempt, a ``reason`` outside :data:`LEGAL_REWARD_REASONS` /
    :data:`LEGAL_REWARD_REASON_PREFIXES`, or a record whose ``done``/``raw`` pair cannot come from the
    reward hook (``done`` must be true, ``raw`` numeric).

    The count rule is a disclosure heuristic, not an invariant: the reward hook POSTs on *every*
    ``core.endEpisode`` call (``miniwob_server.REWARD_HOOK_PATCH``), so a task that keeps calling it
    after the terminal episode can legitimately add records for one load.  Pilot evidence: exactly one
    record per load in all 11 recorded cell runs.
    """
    records = [record for record in _reward_lines(rewards_path) if _matches_cell(record, subdomain=subdomain, seed=seed)]
    records = [record for record in records if _in_window(record, started_at, finished_at)]

    flagged = False
    if len(records) > max(int(page_loads), 1):
        flagged = True
    for record in records:
        reason = record.get("reason")
        if not isinstance(reason, str) or not (reason in LEGAL_REWARD_REASONS or reason.startswith(LEGAL_REWARD_REASON_PREFIXES)):
            flagged = True
        raw = record.get("raw")
        if record.get("done") is not True or raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            flagged = True
    return CellScan(reward_records=len(records), flagged=flagged)


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
    terminal_cue: bool = False,
    single_start: bool = False,
) -> subprocess.Popen[str]:
    """Start the patch server as a sub-process after probing the port (spec §3.1, spec-r2 §4.1).

    The two r2 flags are server-level: they change which patches are appended to ``core.js`` and
    never the URL/seed/episode semantics.
    """
    if probe_port(host, port):
        raise BenchmarkError(f"port {host}:{port} is already in use; free it before the benchmark (the URL is an experiment parameter)")
    command = [
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
    ]
    if terminal_cue:
        command.append("--terminal-cue")
    if single_start:
        command.append("--single-start")
    process = subprocess.Popen(
        command,
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
# C5 role routing (spec-r2 §6) + preflight gate (C1c)
# ---------------------------------------------------------------------------------------------


def build_agents_llm_config(*, nav_model: str, base_url: str, api_type: str, planner_model: str = ROLE_ROUTING_PLANNER_MODEL, helper_model: str = ROLE_ROUTING_HELPER_MODEL) -> dict[str, Any]:
    """The ``ConfigFileLoader``-compatible per-role config (spec-r2 §6.1).

    ``model_api_key`` is **always omitted** (red line): the key reaches the child process through the
    environment only (``MODEL_API_KEY`` for nav/helper via ``create_chat_model``, ``OPENAI_API_KEY``
    for the planner's bare ``ChatOpenAI`` — review-r2 M1).
    """

    def entry(model: str) -> dict[str, Any]:
        return {
            "model_name": model,
            "model_base_url": base_url,
            "model_api_type": api_type,
            "llm_config_params": {"temperature": 0.0, "cache_seed": None},
        }

    return {
        ROLE_ROUTING_REF_KEY: {
            "planner_agent": entry(planner_model),
            "nav_agent": entry(nav_model),
            "helper_agent": entry(helper_model),
        }
    }


def write_agents_llm_config(path: Path, config: Mapping[str, Any]) -> Path:
    """Write the generated config atomically enough for one run and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    lowered = text.lower()
    if "model_api_key" in lowered or "sk-" in text:
        raise BenchmarkError("generated agents_llm_config.json must never contain a key (spec-r2 red line)")
    path.write_text(text, encoding="utf-8")
    return path


def role_routing_env(config_path: Path, api_key: str) -> dict[str, str]:
    """Exactly the four child-env keys of spec-r2 §6.2 (T7: one key more is a failure).

    ``MODEL_API_KEY`` covers nav/helper (``create_chat_model`` fallback); ``OPENAI_API_KEY`` carries
    the same value for the planner's bare ``ChatOpenAI`` (review-r2 M1).  The key travels through
    ``subprocess env=`` only — never into a file.
    """
    cleaned = (api_key or "").strip()
    if not cleaned:
        raise runner_module.RunnerError("empty API key: nothing to inject")
    return {
        "AGENTS_LLM_CONFIG_FILE": str(Path(config_path).resolve()),
        "AGENTS_LLM_CONFIG_FILE_REF_KEY": ROLE_ROUTING_REF_KEY,
        "MODEL_API_KEY": cleaned,
        "OPENAI_API_KEY": cleaned,
    }


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
        terminal_cue: bool = False,
        single_start: bool = False,
        role_routing: bool = False,
        nav_model: str | None = None,
        extra_tools: bool = False,
        extra_tools_modules: str = DEFAULT_EXTRA_TOOLS_MODULES,
        nav_max_tokens: int = 0,
        planner_timeout: int = 0,
        disable_sandbox: bool = False,
        assert_discipline: bool = False,
        template_notes: bool = False,
        latency_env: bool = False,
        smoke_cells: Sequence[str] = (),
        provider: str | runner_module.LLMProviderConfig | None = None,
        max_cells: int | None = None,
    ) -> None:
        if stage not in tasks_module.STAGES:
            raise BenchmarkError(f"unknown stage: {stage!r} (expected one of {tasks_module.STAGES})")
        smoke = [str(subdomain).strip() for subdomain in smoke_cells if str(subdomain).strip()]
        if smoke and stage != "pilot":
            raise BenchmarkError("--smoke-cells is a pilot-only appendix (spec-r2 §4.1)")
        if len(set(smoke)) != len(smoke):
            raise BenchmarkError(f"duplicate --smoke-cells entries: {smoke}")
        self.exp_id = exp_id
        self.stage = stage
        #: r2: LLM provider of this experiment (``None`` = deepseek 现状).  Drives the key file, the
        #: child-env model/base_url, the C1c preflight target and the C5 routing defaults.
        self.provider = runner_module.resolve_provider(provider)
        if self.provider is runner_module.DEEPSEEK:
            self.planner_model = ROLE_ROUTING_PLANNER_MODEL
            self.helper_model = ROLE_ROUTING_HELPER_MODEL
            nav_model_default = DEFAULT_NAV_MODEL
        else:  # glm (r2): planner keeps the flagship model, nav/helper ride the provider's flash tier
            self.planner_model = GLM_PLANNER_MODEL
            self.helper_model = self.provider.model
            nav_model_default = self.provider.model
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
        self.terminal_cue = bool(terminal_cue)
        self.single_start = bool(single_start)
        self.role_routing = bool(role_routing)
        self.nav_model = str(nav_model).strip() if nav_model and str(nav_model).strip() else nav_model_default
        self.extra_tools = bool(extra_tools)
        # spec-r3 §5.1 (E1): the subset csv is only consumed with --extra-tools; an empty value with
        # --extra-tools would silently re-open the full (r2) tool surface, so it is a hard error.
        self.extra_tools_modules_csv = str(extra_tools_modules).strip()
        if self.extra_tools and not self.extra_tools_modules_csv:
            raise BenchmarkError("--extra-tools-modules must not be empty (pass 'all' for the full r2 load)")
        self.extra_tools_modules: list[str] = []
        if self.extra_tools:
            if self.extra_tools_modules_csv.lower() == "all":
                self.extra_tools_modules = [EXTRA_TOOLS_MODULES_ALL]
            else:
                self.extra_tools_modules = [name.strip() for name in self.extra_tools_modules_csv.split(",") if name.strip()]
        # spec-r3 §1.2/§2.4/§5.5/§6: 0/off = r2 behaviour; the headline turns each on explicitly.
        self.nav_max_tokens = int(nav_max_tokens)
        self.planner_timeout = int(planner_timeout)
        self.disable_sandbox = bool(disable_sandbox)
        self.assert_discipline = bool(assert_discipline)
        self.template_notes = bool(template_notes)
        self.latency_env = bool(latency_env)
        self.max_cells = int(max_cells) if max_cells else None
        self.smoke_subdomains = smoke
        #: spec-r2 §4.1: every switch lands in the manifest ``flags`` object (headline = all-on).
        #: spec-r3 §7 adds the five r3 keys (off/empty semantics on the default run).
        self.flags: dict[str, Any] = {
            "terminal_cue": self.terminal_cue,
            "single_start": self.single_start,
            "role_routing": self.role_routing,
            "nav_model": self.nav_model,
            "extra_tools": self.extra_tools,
            "template_notes": self.template_notes,
            "latency_env": self.latency_env,
            "smoke_cells": list(self.smoke_subdomains),
            "nav_max_tokens": self.nav_max_tokens,
            "planner_timeout": self.planner_timeout,
            "extra_tools_modules": list(self.extra_tools_modules),
            "disable_sandbox": self.disable_sandbox,
            "assert_discipline": self.assert_discipline,
        }
        self.tasks = [dict(task) for task in tasks] if tasks is not None else tasks_module.load_tasks()
        self.stage_tasks = tasks_module.select_stage(stage, self.tasks)
        self.started_at = _now()
        self.hercules_runs = 0
        self.retries_used = 0
        self.consecutive_breaks = 0
        self._routing_config_path: Path | None = None

    # -- public entry ------------------------------------------------------------------------

    def run(self) -> int:
        """Execute the stage; returns a process exit code."""
        budget = hercules_budget(self.stage, extra_runs=len(self._smoke_cells_planned()))
        assert_budget(budget["total"])
        cells = self._plan_stage_cells()
        if self.dry_run:
            return self._print_plan(cells, budget)

        # spec-r2 §0.1 (C1c): the balance preflight is a hard gate — no server, no cell on failure.
        preflight_code = self._run_preflight()
        if preflight_code != 0:
            return preflight_code
        if self.role_routing:
            self._routing_config_path = self._prepare_role_routing()

        self.exp_dir.mkdir(parents=True, exist_ok=True)
        server = start_miniwob_server(
            root=self.html_root,
            port=self.port,
            rewards_file=self.rewards_path,
            host=self.host,
            terminal_cue=self.terminal_cue,
            single_start=self.single_start,
        )
        try:
            executed = 0
            for cell in cells:
                if self.max_cells is not None and executed >= self.max_cells:
                    logger.info("orchestrator: --max-cells %s reached (%s new cells done), stopping early — resume by rerunning the same command", self.max_cells, executed)
                    break
                self._run_cell(cell)
                executed += 1
            self._retry_infrastructure_failures()
        finally:
            stop_miniwob_server(server)
        self._write_manifest()
        logger.info(
            "orchestrator: stage %s finished, %s Hercules runs used, %s retries (cap %s)",
            self.stage,
            self.hercules_runs,
            self.retries_used,
            R2_BUDGET_CAP,
        )
        return 0

    # -- preflight / role routing (C1c / C5) ---------------------------------------------------

    def _run_preflight(self) -> int:
        """Probe every model this run will use; exit 3 (masked reason) when any probe fails."""
        try:
            key = runner_module.read_api_key(self.provider.key_path)
        except runner_module.RunnerError as exc:
            logger.error("preflight failed: %s — 请充值或更换可用 key 后重试", exc)
            return 3
        models = [self.provider.model]
        if self.role_routing:
            # 探全部将使用的模型（security-review-r2-pre W1）：nav 与 planner（helper 与 nav 同档时已覆盖）
            for used in (self.nav_model, self.planner_model):
                if used not in models:
                    models.append(used)
        for model in models:
            result = preflight_module.probe_llm(api_key=key, model=model, base_url=self.provider.base_url)
            if not result.ok:
                logger.error("preflight failed for model %s: %s — 请充值或更换可用 key 后重试", model, result.detail)
                return 3
        logger.info("orchestrator: preflight ok for %s", ", ".join(models))
        return 0

    def _prepare_role_routing(self) -> Path:
        """Generate ``<exp_dir>/agents_llm_config.json`` (no key inside; spec-r2 §6.1)."""
        config = build_agents_llm_config(
            nav_model=self.nav_model,
            base_url=self.provider.base_url,
            api_type=runner_module.LLM_MODEL_API_TYPE,
            planner_model=self.planner_model,
            helper_model=self.helper_model,
        )
        return write_agents_llm_config(self.exp_dir / AGENTS_LLM_CONFIG_FILENAME, config)

    def _child_extra_env(self) -> dict[str, str]:
        """Merged ``extra_env`` of every enabled r2/r3 flag (spec-r2 §5.3/§6.2/§7.1, spec-r3 §7).

        r3 injection precision (locked by T10): the five new keys appear only with their flag on;
        ``extra_tools_modules=["__all__"]`` (the ``all`` csv) injects ``LOAD_EXTRA_TOOLS`` but **no**
        ``EXTRA_TOOLS_MODULES`` — the full r2 load.
        """
        extra: dict[str, str] = {}
        if self.latency_env:
            extra.update(LATENCY_ENV_OVERRIDES)
        if self.extra_tools:
            extra["LOAD_EXTRA_TOOLS"] = "true"
            if self.extra_tools_modules != [EXTRA_TOOLS_MODULES_ALL]:
                extra["EXTRA_TOOLS_MODULES"] = self.extra_tools_modules_csv
        if self.nav_max_tokens > 0:
            extra["NAV_MAX_COMPLETION_TOKENS"] = str(self.nav_max_tokens)
        if self.planner_timeout > 0:
            extra["LLM_PLANNER_REQUEST_TIMEOUT"] = str(self.planner_timeout)
        if self.disable_sandbox:
            extra["SANDBOX_DISABLED"] = "true"
        if self.assert_discipline:
            extra["PLANNER_ASSERT_DISCIPLINE"] = "true"
        if self.role_routing and self._routing_config_path is not None:
            extra.update(role_routing_env(self._routing_config_path, runner_module.read_api_key(self.provider.key_path)))
        return extra

    # -- plan / resume -----------------------------------------------------------------------

    def _smoke_cells_planned(self, existing_keys: Sequence[tuple[str, int]] = ()) -> list[Cell]:
        """Named ``--smoke-cells`` appendix cells (spec-r2 §4.1, review-r2 M3): pilot order + these."""
        done = set() if self.force else {(str(task_id), int(seed)) for task_id, seed in existing_keys}
        cells: list[Cell] = []
        stage_ids = {task["task_id"] for task in self.stage_tasks}
        for subdomain in self.smoke_subdomains:
            matches = [task for task in self.tasks if str(task["subdomain"]) == subdomain]
            if not matches:
                raise BenchmarkError(f"smoke cell not in the task table: {subdomain!r}")
            task = matches[0]
            task_id = str(task["task_id"])
            seed = tasks_module.derive_seed(self.exp_id, task_id)
            if task_id in stage_ids or (task_id, seed) in done:
                continue  # already part of the stage plan or already recorded — idempotent appendix
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

    def _plan_stage_cells(self) -> list[Cell]:
        """Stage cells (resume-aware) plus the smoke appendix, in that deterministic order."""
        existing = self._existing_cells()
        cells = plan_cells(self.exp_id, self.stage, tasks=self.tasks, existing_keys=existing, force=self.force)
        return cells + self._smoke_cells_planned(existing)

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
        limit = tasks_module.STAGE_RUNS[self.stage] + RETRY_BUDGET[self.stage] + len(self.smoke_subdomains)
        if self.hercules_runs >= min(limit, R2_BUDGET_CAP):
            raise BenchmarkError(f"budget breach: refusing Hercules run {self.hercules_runs + 1} (stage limit {limit}, cap {R2_BUDGET_CAP})")

    def _run_cell(self, cell: Cell) -> dict[str, Any]:
        started_at = _now()
        started = time.monotonic()
        try:
            goal = goal_reader.read_goal(cell.subdomain, cell.seed, port=self.port, episode_ms=self.episode_ms)
        except goal_reader.GoalReadError as exc:
            # spec-r3 §5.6 (E6): persist the extreme-early-crash evidence (e.g. the r2 0.6s
            # email-inbox-forward-nl cell that left zero logs).  Failure to write is a warning only —
            # the result row's failure_message semantics stay untouched.
            try:
                error_log_dir = self.runs_dir / cell.run_id
                error_log_dir.mkdir(parents=True, exist_ok=True)
                (error_log_dir / "goal_read_error.log").write_text(str(exc) + "\n", encoding="utf-8")
            except OSError as write_error:
                logger.warning("orchestrator: could not write goal_read_error.log for %s: %s", cell.label, write_error)
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
                model=self.provider.model,
                attempt=self._attempt_no(cell),
            )
            self._append_row(row)
            logger.warning("orchestrator: %s -> no_goal (%s)", cell.label, exc)
            return row

        row = self._execute_cell(cell, goal=goal, started_at=started_at, started=started)
        self._append_row(row)
        logger.info("orchestrator: %s -> %s (%.1fs, reward=%s)", cell.run_id, row["status"], time.monotonic() - started, row["reward_raw"])
        # spec-r2 §2.2 run-level breaker: two consecutive infra-break rows abort the stage (exit 2).
        if row.get("infra_circuit_break") is True:
            self.consecutive_breaks += 1
            if self.consecutive_breaks >= CIRCUIT_BREAK_RUN_LIMIT:
                raise BenchmarkError(f"circuit breaker: {self.consecutive_breaks} consecutive infra breaks (402/balance/connection); aborting the stage, recorded rows are kept")
        else:
            self.consecutive_breaks = 0
        return row

    def _execute_cell(self, cell: Cell, *, goal: str, started_at: str, started: float) -> dict[str, Any]:
        self._assert_can_run()
        attempt_no = self._attempt_no(cell)
        feature_text = goal_reader.render_feature(
            task_id=cell.task_id,
            subdomain=cell.subdomain,
            seed=cell.seed,
            port=self.port,
            episode_ms=self.episode_ms,
            goal=goal,
            notes=self.template_notes,
            notes_terminal_cue=self.template_notes and self.terminal_cue,
        )
        self.features_dir.mkdir(parents=True, exist_ok=True)
        (self.features_dir / f"{cell.run_id}.feature").write_text(feature_text, encoding="utf-8")

        project_root = runner_module.prepare_run_dir(self.runs_dir / cell.run_id / "opt")
        feature_path = project_root / "input" / f"{cell.run_id}.feature"
        feature_path.write_text(feature_text, encoding="utf-8")

        attempt_log_path = self._stdout_log_path(cell, attempt=attempt_no)
        self.hercules_runs += 1
        result = runner_module.run_feature(
            feature_path,
            run_id=cell.run_id,
            project_root=project_root,
            timeout_s=self.timeout_s,
            extra_env=self._child_extra_env() or None,
            stdout_log_path=attempt_log_path,
            provider=self.provider,
        )
        junit_passed, junit_terminate = _junit_verdict(result)
        reward = fetch_reward(self.base_url, task=cell.subdomain, seed=cell.seed)
        finished_at = _now()
        broken = _attempt_circuit_break(result, stdout_log_path=attempt_log_path)
        if broken:
            logger.warning("orchestrator: %s attempt %s circuit-broken (fast infra death with a 402/connection marker)", cell.label, attempt_no)
        scan = self._scan_cell(cell, attempt=attempt_no, started_at=started_at, finished_at=finished_at)
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
            finished_at=finished_at,
            model=self.provider.model,
            task_url_navigations=scan.task_url_navigations,
            flagged=scan.flagged,
            invalid_reason=scan.invalid_reason,
            attempt=attempt_no,
            infra_circuit_break=broken,
        )

    # -- cell integrity scan (安全审查 R1 §4.2/§4.3) ------------------------------------------

    def _stdout_log_path(self, cell: Cell, *, attempt: int = 1) -> Path:
        """This attempt's child log (``runs/<run_id>/attempt<N>/stdout.log``, spec-r2 §2.1)."""
        return self.runs_dir / cell.run_id / f"attempt{max(1, int(attempt))}" / "stdout.log"

    def _scan_cell(self, cell: Cell, *, attempt: int = 1, started_at: str | None = None, finished_at: str | None = None) -> CellScan:
        """H2 + H3 for one cell, both over artefacts that are already on disk (no network, no browser).

        H2 reads only this attempt's log (C1d); the H3 window filter already isolates this attempt's
        reward records.  A flagged cell is disclosed on the row and logged; only the V5/V6 security
        events and the r3 file-tool call markers make it invalid (``invalid_reason``) — the official
        reward and ``status`` are never rewritten.
        """
        log_scan = scan_cell_log(self._stdout_log_path(cell, attempt=attempt), subdomain=cell.subdomain, seed=cell.seed)
        reward_scan = scan_cell_rewards(
            self.rewards_path,
            subdomain=cell.subdomain,
            seed=cell.seed,
            page_loads=log_scan.task_url_navigations,
            started_at=started_at,
            finished_at=finished_at,
        )
        scan = log_scan.merge(reward_scan)
        if scan.invalid_reason:
            logger.warning(
                "orchestrator: SECURITY EVENT for %s -> cell invalid (%s); navigations=%s reward_records=%s",
                cell.label,
                scan.invalid_reason,
                scan.task_url_navigations,
                scan.reward_records,
            )
        elif scan.flagged:
            logger.warning(
                "orchestrator: %s flagged (renavigation=%s reward_records=%s; V3/V4 disclosed, official reward kept)",
                cell.label,
                scan.task_url_navigations,
                scan.reward_records,
            )
        return scan

    def _task_for(self, task_id: str) -> dict[str, Any]:
        for task in self.tasks:
            if str(task["task_id"]) == task_id:
                return dict(task)
        raise BenchmarkError(f"task not in the table: {task_id}")

    def _attempt_no(self, cell: Cell) -> int:
        """``<existing rows for this cell> + 1`` (spec-r2 §2.1): attempt 1 without retries."""
        count = 0
        if self.results_path.is_file():
            for row in metrics_module.load_rows(self.results_path):
                seed = row.get("seed")
                if str(row.get("task_id")) == cell.task_id and seed == cell.seed and isinstance(seed, int) and not isinstance(seed, bool):
                    count += 1
        return count + 1

    # -- retries / manifest ------------------------------------------------------------------

    def _retry_infrastructure_failures(self) -> None:
        """Re-run infrastructure cells (timeout/no_junit/no_reward) once each, within the retry buffer.

        spec-r2 §2.2: circuit-broken rows (fast 402/connection deaths) are explicitly excluded —
        retrying them would only burn the buffer against a dead key.  Smoke appendix cells
        (``--smoke-cells``) never retry either (spec-r2 §4.1).
        """
        retry_budget = RETRY_BUDGET[self.stage]
        if retry_budget <= 0 or not self.results_path.is_file():
            return
        smoke_keys = {(cell.task_id, cell.seed) for cell in self._smoke_cells_planned()}
        attempts: dict[tuple[str, int], int] = {}
        broken_keys: set[tuple[str, int]] = set()
        for row in metrics_module.load_rows(self.results_path):
            seed = row.get("seed")
            if not isinstance(seed, int) or isinstance(seed, bool):
                continue
            key = (str(row.get("task_id")), seed)
            attempts[key] = attempts.get(key, 0) + 1
            if row.get("infra_circuit_break") is True:
                broken_keys.add(key)
        infra_cells: list[Cell] = []
        for row in metrics_module.load_rows(self.results_path):
            if row.get("status") not in INFRA_STATUSES:
                continue
            seed = row.get("seed")
            if not isinstance(seed, int) or isinstance(seed, bool):
                continue
            key = (str(row.get("task_id")), seed)
            if key in broken_keys:
                logger.info("orchestrator: %s is circuit-broken, excluded from the retry pool", key[0])
                continue
            if key in smoke_keys:
                continue
            if attempts.get(key, 0) > INFRA_RETRY_LIMIT_PER_CELL:
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
        manifest: dict[str, Any] = {
            "exp_id": self.exp_id,
            "git_rev": git_rev(),
            "started_at": self.started_at,
            "stage": self.stage,
            "model_name": self.provider.model,
            "llm_base_url": self.provider.base_url,
            "server_port": self.port,
            "episode_max_time_ms": self.episode_ms,
            "timeout_s": self.timeout_s,
            "flags": dict(self.flags),
            "budget": {
                "hercules_used": self.hercules_runs,
                "retries_used": self.retries_used,
                "stage_plan": tasks_module.STAGE_RUNS[self.stage],
                "retry_budget": RETRY_BUDGET[self.stage],
                "cap": R2_BUDGET_CAP,
            },
            "cells": [{"task_id": cell.task_id, "seed": cell.seed} for cell in plan_cells(self.exp_id, self.stage, tasks=self.tasks, force=True)],
            "metrics": summary.as_dict(),
            "finished_at": _now(),
        }
        if self.provider is not runner_module.DEEPSEEK:
            # 口径披露（r2）：非默认 provider 时 manifest 追加 provider 名与实际模型；deepseek 现状
            # 的 manifest 逐字节不变（model_name/llm_base_url 已隐含披露）。
            manifest["llm_provider"] = self.provider.name
            manifest["model"] = self.provider.model
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
    parser = argparse.ArgumentParser(description="MiniWoB++ benchmark orchestration (spec §7, spec-r2 §4.1)")
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--stage", required=True, choices=list(tasks_module.STAGES))
    parser.add_argument("--dry-run", action="store_true", help="print the cell plan and the budget without touching anything (skips the C1c preflight)")
    parser.add_argument("--force", action="store_true", help="ignore existing results.jsonl rows and re-plan every cell")
    parser.add_argument("--exp-root", default=str(DEFAULT_EXP_ROOT))
    parser.add_argument("--html-root", default=str(MINIWOB_HTML_ROOT))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--episode-ms", type=int, default=tasks_module.EPISODE_MAX_TIME_MS_DEFAULT)
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S)
    # -- r2 switch set (spec-r2 §4.1); all default off, headline turns them on explicitly ----------
    parser.add_argument("--terminal-cue", action="store_true", help="C2: serve core.js with the neutral EPISODE ENDED terminal cue")
    parser.add_argument("--single-start", action="store_true", help="C3: auto-start the episode at most once per tab")
    parser.add_argument("--role-routing", action="store_true", help="C5: per-role model routing (generates agents_llm_config.json, key stays in env)")
    parser.add_argument("--nav-model", default=None, help="C5: the routed nav/executor model name (default: deepseek-flash for deepseek, glm-5.3-flash for glm; probed by the C1c preflight)")
    parser.add_argument(
        "--provider",
        default="deepseek",
        choices=sorted(runner_module.PROVIDERS),
        help="r2: LLM provider of this experiment (default deepseek keeps the r1 status quo; glm reads GLM-Key.txt and rides the coding-plan endpoint)",
    )
    parser.add_argument("--extra-tools", action="store_true", help="C6: load extra_tools in the child (drag_and_drop etc., LOAD_EXTRA_TOOLS=true)")
    parser.add_argument("--template-notes", action="store_true", help="C7: append the fixed context notes block to the generated feature")
    parser.add_argument("--latency-env", action="store_true", help="C4f: inject the latency pack env (timeout/retries/refresh mode/round cap/step budget)")
    parser.add_argument("--smoke-cells", default="", help="comma-separated subdomains appended to the pilot plan (pilot only, never retried)")
    parser.add_argument("--max-cells", type=int, default=None, help="stop after N new cells this invocation (pacing for 5h-window quotas; resume by rerunning the same command)")
    # -- r3 switch set (spec-r3 §7); all default off (= r2 behaviour), headline turns them on --------
    parser.add_argument("--nav-max-tokens", type=int, default=0, help="R3-1: NAV_MAX_COMPLETION_TOKENS cap for nav/executor chat models (0 = r2 behaviour, adapt default 4096)")
    parser.add_argument("--planner-timeout", type=int, default=0, help="R3-2: LLM_PLANNER_REQUEST_TIMEOUT seconds for the planner wait_for AND provider timeout (0 = follow LLM_REQUEST_TIMEOUT)")
    parser.add_argument(
        "--extra-tools-modules",
        default=DEFAULT_EXTRA_TOOLS_MODULES,
        help="E1: csv extra_tools subset loaded with --extra-tools (default drag_and_drop_tool only; 'all' = full r2 load, empty is an error)",
    )
    parser.add_argument("--disable-sandbox", action="store_true", help="E5: inject SANDBOX_DISABLED=true (a blocked attempt still invalidates the cell)")
    parser.add_argument("--assert-discipline", action="store_true", help="R3-6/F: PLANNER_ASSERT_DISCIPLINE=true planner prompt preamble")
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
            terminal_cue=args.terminal_cue,
            single_start=args.single_start,
            role_routing=args.role_routing,
            nav_model=args.nav_model,
            extra_tools=args.extra_tools,
            extra_tools_modules=args.extra_tools_modules,
            nav_max_tokens=args.nav_max_tokens,
            planner_timeout=args.planner_timeout,
            disable_sandbox=args.disable_sandbox,
            assert_discipline=args.assert_discipline,
            template_notes=args.template_notes,
            latency_env=args.latency_env,
            smoke_cells=[cell for cell in args.smoke_cells.split(",") if cell.strip()],
            provider=args.provider,
            max_cells=args.max_cells,
        )
        return orchestrator.run()
    except (BenchmarkError, tasks_module.BenchmarkError, runner_module.RunnerError) as exc:
        # RunnerError = unusable executor environment (missing key file, unusable paths): no traceback
        logger.error("orchestrator: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
