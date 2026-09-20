"""Hercules sub-process executor and JUnit XML parsing (spec §4, §6.3).

Layout of one run (spec §4.2/§8)::

    dev_runs/experiments/<exp_id>/runs/<run_id>/
      opt/                  # passed as --project-base; input/, output/, proofs/, log_files/ inside
      stdout.log            # child stdout+stderr, masked with the API key

The API key never reaches ``argv``: it is injected through ``env=`` only (spec §4.3), and every
captured log passes through :func:`mask_secret` before it is written to disk.

``parse_junit_xml`` raises :class:`JUnitParseError` for malformed XML; :func:`run_feature` maps that
to ``status="no_junit"`` (never to a silent pass, spec §4.2).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from testzeus_hercules.utils.logger import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
#: Spec §4.3: the key file is a single line at the repository root (git-ignored).
LLM_KEY_PATH = REPO_ROOT / "LLM-Key.txt"

HERCULES_MODULE = "testzeus_hercules"
DEFAULT_TIMEOUT_S = 900
REDACTED = "***REDACTED***"
#: Secret fragments shorter than this are left alone: redacting 1-2 chars would shred every log.
MASK_FRAGMENT_LEN = 8

#: §4.3 fixed LLM plumbing; D2 pilot confirms the model string / base URL combination.
LLM_MODEL_NAME = "openai/deepseek-chat"
LLM_MODEL_BASE_URL = "https://api.deepseek.com"
LLM_MODEL_API_TYPE = "openai"

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_NO_JUNIT = "no_junit"
#: ``run_feature(dry_run=True)`` only builds the command/env; such rows must never enter results.jsonl.
STATUS_DRY_RUN = "dry_run"

#: Flattened litellm cost keys (spec §6.3).
COST_KEY = "usage_including_cached_inference.total_cost"
COST_FALLBACK_SUITE_KEY = "total_execution_cost"
TOKEN_PREFIX = "usage_including_cached_inference."
TOKEN_SUFFIX = ".total_tokens"
TERMINATE_PROPERTY = "Terminate"
FINAL_RESPONSE_PROPERTY = "final_response"
FINAL_RESPONSE_STDOUT_PREFIX = "Final Response:"

_PROPERTY_CONTAINER = "properties"
_PROPERTY_TAG = "property"
_TESTCASE_TAG = "testcase"
_FAILURE_TAGS = ("failure", "error")


class RunnerError(RuntimeError):
    """Unrecoverable executor error (missing LLM key, unusable paths)."""


class JUnitParseError(ValueError):
    """Raised when a JUnit XML artefact cannot be parsed or carries no testcase (spec §4.2)."""


@dataclass(frozen=True)
class RunPlan:
    """Everything needed to launch one Hercules run, without launching it (``--dry-run``)."""

    feature_path: Path
    project_root: Path
    run_dir: Path
    output_path: Path
    junit_path: Path
    cmd: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]

    def env_redacted(self) -> dict[str, str]:
        """The child env with the API key masked, safe to log (spec §8: manifest 不含 key)."""
        key = self.env.get("LLM_MODEL_API_KEY", "")
        return {name: mask_secret(value, key) for name, value in self.env.items()}


@dataclass(frozen=True)
class RunResult:
    """Outcome of one ``run_feature`` call (spec §4.1)."""

    run_id: str
    status: str
    passed: bool
    duration_s: float | None
    cost_usd: float | None
    total_tokens: int | None
    junit_xml: str | None
    failure_message: str | None
    run_dir: str


# ---------------------------------------------------------------------------------------------
# Key handling (spec §4.3)
# ---------------------------------------------------------------------------------------------


def read_api_key(path: Path | None = None) -> str:
    """Read the LLM key from ``LLM-Key.txt`` (single line; ``strip()`` is the key)."""
    key_path = Path(path) if path is not None else LLM_KEY_PATH
    try:
        return key_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RunnerError(f"cannot read LLM key from {key_path}: {exc}") from exc


def mask_secret(text: str, secret: str) -> str:
    """Replace every occurrence of ``secret`` (and of its head/tail fragments) with ``***REDACTED***``.

    A blank secret leaves the text untouched (spec §9 E28).
    """
    if not text:
        return text
    cleaned = (secret or "").strip()
    if not cleaned:
        return text
    masked = text.replace(cleaned, REDACTED)
    if len(cleaned) >= MASK_FRAGMENT_LEN * 2:
        for fragment in (cleaned[:MASK_FRAGMENT_LEN], cleaned[-MASK_FRAGMENT_LEN:]):
            masked = masked.replace(fragment, REDACTED)
    return masked


def build_child_env(api_key: str) -> dict[str, str]:
    """Child environment for the Hercules sub-process (spec §4.3): ``os.environ`` plus LLM plumbing.

    The key travels only here — never in ``argv``, never in a written artefact.
    """
    if not (api_key or "").strip():
        raise RunnerError("empty API key: nothing to inject")
    env = dict(os.environ)
    env.update(
        {
            "LLM_MODEL_NAME": LLM_MODEL_NAME,
            "LLM_MODEL_BASE_URL": LLM_MODEL_BASE_URL,
            "LLM_MODEL_API_TYPE": LLM_MODEL_API_TYPE,
            "LLM_MODEL_API_KEY": api_key.strip(),
            "ENABLE_TELEMETRY": "0",  # config import initialises Sentry otherwise (telemetry.py:20,65)
            "HEADLESS": "true",
        }
    )
    return env


# ---------------------------------------------------------------------------------------------
# Run construction (spec §4.2)
# ---------------------------------------------------------------------------------------------


def build_command(feature_path: Path, project_root: Path) -> tuple[str, ...]:
    """The Hercules CLI invocation; absolute paths, key never present (spec §4.2)."""
    feature = Path(feature_path).resolve()
    project = Path(project_root).resolve()
    output = project / "output"
    return (
        sys.executable,
        "-u",
        "-m",
        HERCULES_MODULE,
        "--input-file",
        str(feature),
        "--project-base",
        str(project),
        "--output-path",
        str(output),
    )


def junit_path_for(feature_path: Path, project_root: Path) -> Path:
    """直连候选路径 ``<output-path>/<feature 文件名>_result.xml``（spec §4.2 / ``__main__.py:118``）。

    注：上游 ``get_junit_xml_base_path()`` 返回 ``<PROJECT_SOURCE_ROOT>/output/<run_TS>``（时间戳子目录），
    因此真实产物在 ``<output>/run_*/`` 下；:func:`find_junit_xml` 负责两级定位。
    """
    return Path(project_root).resolve() / "output" / (Path(feature_path).name + "_result.xml")


def find_junit_xml(feature_path: Path, project_root: Path) -> Path | None:
    """Locate the JUnit artefact of one run: direct path, then timestamp subdirs (newest wins).

    Each run owns an isolated project root, so at most one artefact is expected; ``newest wins`` is a
    deterministic tie-breaker for the pathological case (documented in the test report).
    """
    direct = junit_path_for(feature_path, project_root)
    if direct.is_file():
        return direct
    output_dir = Path(project_root).resolve() / "output"
    name = Path(feature_path).name + "_result.xml"
    nested = sorted(output_dir.glob(f"*/{name}"), key=lambda path: path.stat().st_mtime, reverse=True)
    if nested:
        return nested[0]
    return None


def prepare_run_dir(project_root: Path) -> Path:
    """Create the per-run project root with ``input/`` and ``output/``; returns the project root."""
    project = Path(project_root).resolve()
    (project / "input").mkdir(parents=True, exist_ok=True)
    (project / "output").mkdir(parents=True, exist_ok=True)
    return project


def build_run_plan(feature_path: Path, project_root: Path, *, api_key: str | None = None) -> RunPlan:
    """Assemble cmd/cwd/env for one run without executing anything (``--dry-run`` path)."""
    feature = Path(feature_path).resolve()
    if not feature.is_file():
        raise RunnerError(f"feature file not found: {feature}")
    project = prepare_run_dir(project_root)
    key = read_api_key() if api_key is None else api_key
    return RunPlan(
        feature_path=feature,
        project_root=project,
        run_dir=project.parent,
        output_path=project / "output",
        junit_path=junit_path_for(feature, project),
        cmd=build_command(feature, project),
        cwd=REPO_ROOT,
        env=build_child_env(key),
    )


# ---------------------------------------------------------------------------------------------
# Execution (spec §4.2)
# ---------------------------------------------------------------------------------------------


def run_feature(
    feature_path: Path,
    *,
    run_id: str,
    project_root: Path,
    extra_env: Mapping[str, str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    api_key: str | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Run one feature with Hercules in a sub-process and collect the JUnit outcome (spec §4.1).

    ``dry_run=True`` only builds the plan: nothing is executed and ``status`` is ``"dry_run"``.
    ``extra_env`` is merged over the generated child env (last write wins) and its values are masked
    in the log; it exists for sweep-level overrides, not for key transport.
    """
    plan = build_run_plan(feature_path, project_root, api_key=api_key)
    env = dict(plan.env)
    if extra_env:
        env.update({str(name): str(value) for name, value in extra_env.items()})
    key = env.get("LLM_MODEL_API_KEY", "")

    if dry_run:
        logger.info("runner: dry-run for %s -> %s", run_id, " ".join(plan.cmd))
        return RunResult(
            run_id=run_id,
            status=STATUS_DRY_RUN,
            passed=False,
            duration_s=None,
            cost_usd=None,
            total_tokens=None,
            junit_xml=None,
            failure_message=None,
            run_dir=str(plan.run_dir),
        )

    started = time.monotonic()
    timed_out = False
    stdout = ""
    process = subprocess.Popen(
        list(plan.cmd),
        cwd=str(plan.cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group: timeout kills browser children too
    )
    try:
        stdout, _ = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        try:
            stdout, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            stdout = ""
    wall_clock = time.monotonic() - started

    masked = mask_secret(stdout or "", key)
    _write_stdout_log(plan.run_dir / "stdout.log", masked, run_id=run_id, timed_out=timed_out, returncode=process.returncode)

    return _collect_result(
        run_id=run_id,
        plan=plan,
        masked_stdout=masked,
        timed_out=timed_out,
        returncode=process.returncode,
        wall_clock=wall_clock,
        timeout_s=timeout_s,
    )


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - already gone
        process.kill()


def _write_stdout_log(path: Path, masked_stdout: str, *, run_id: str, timed_out: bool, returncode: int | None) -> None:
    header = [f"# run_id: {run_id}", f"# timed_out: {timed_out}", f"# returncode: {returncode}", ""]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(header) + masked_stdout, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - disk issues only
        logger.warning("runner: cannot write stdout log %s: %s", path, exc)


def _collect_result(
    *,
    run_id: str,
    plan: RunPlan,
    masked_stdout: str,
    timed_out: bool,
    returncode: int | None,
    wall_clock: float,
    timeout_s: int,
) -> RunResult:
    tail = _tail(masked_stdout, limit=4000)
    if timed_out:
        message = f"timeout after {timeout_s}s (process group killed; returncode={returncode})\n{tail}"
        return RunResult(run_id, STATUS_TIMEOUT, False, round(wall_clock, 3), None, None, None, message, str(plan.run_dir))

    junit_path = find_junit_xml(plan.feature_path, plan.project_root)
    if junit_path is None:
        message = f"junit xml missing under {plan.output_path} (returncode={returncode})\n{tail}"
        return RunResult(run_id, STATUS_NO_JUNIT, False, round(wall_clock, 3), None, None, None, message, str(plan.run_dir))

    try:
        parsed = parse_junit_xml(junit_path)
    except JUnitParseError as exc:
        message = f"junit xml unreadable: {exc}\n{tail}"
        return RunResult(run_id, STATUS_NO_JUNIT, False, round(wall_clock, 3), None, None, str(junit_path), message, str(plan.run_dir))

    duration = parsed.get("duration_s")
    return RunResult(
        run_id=run_id,
        status=STATUS_PASSED if parsed["passed"] else STATUS_FAILED,
        passed=bool(parsed["passed"]),
        duration_s=float(duration) if duration is not None else round(wall_clock, 3),
        cost_usd=parsed.get("cost_usd"),
        total_tokens=parsed.get("total_tokens"),
        junit_xml=str(junit_path),
        failure_message=parsed.get("failure_message"),
        run_dir=str(plan.run_dir),
    )


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


# ---------------------------------------------------------------------------------------------
# JUnit parsing (spec §4.1, §6.3)
# ---------------------------------------------------------------------------------------------


def parse_junit_xml(junit_path: Path) -> dict[str, Any]:
    """Parse a Hercules JUnit artefact (spec §4.1/§6.3).

    ``passed`` is true iff no testcase carries a ``<failure>``/``<error>`` child (spec §4.2).  Cost uses
    the single key ``usage_including_cached_inference.total_cost`` with a suite-level
    ``total_execution_cost`` fallback; tokens follow ``junit_helper.py:154-158`` (prefix-qualified
    keys only, summed) so incl/excl-cached usage is never double counted.
    """
    path = Path(junit_path)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise JUnitParseError(f"cannot parse {path}: {exc}") from exc

    testcases = [element for element in root.iter() if _local_name(element.tag) == _TESTCASE_TAG]
    if not testcases:
        raise JUnitParseError(f"no <testcase> element in {path}")

    suite_props = _suite_properties(root, testcases)
    case_props = _testcase_properties(testcases)

    failures: list[str] = []
    for case in testcases:
        for child in case:
            if _local_name(child.tag) in _FAILURE_TAGS:
                message = child.get("message") or (child.text or "").strip() or _local_name(child.tag)
                failures.append(message)

    durations = [_as_float(case.get("time")) for case in testcases]
    duration_s = sum(value for value in durations if value is not None) if any(value is not None for value in durations) else _as_float(root.get("time"))

    cost = _as_float(case_props.get(COST_KEY))
    if cost is None:
        cost = _as_float(suite_props.get(COST_FALLBACK_SUITE_KEY))

    return {
        "passed": not failures,
        "terminate": case_props.get(TERMINATE_PROPERTY),
        "failure_message": failures[0] if failures else None,
        "final_response": _final_response(case_props, testcases),
        "duration_s": duration_s,
        "cost_usd": cost,
        "total_tokens": _total_tokens(case_props),
        "testcase_count": len(testcases),
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _properties_of(element: ET.Element) -> dict[str, str]:
    props: dict[str, str] = {}
    for container in element:
        if _local_name(container.tag) != _PROPERTY_CONTAINER:
            continue
        for prop in container:
            if _local_name(prop.tag) != _PROPERTY_TAG:
                continue
            name = prop.get("name")
            if name is None:
                continue
            props[name] = prop.get("value", "")
    return props


def _testcase_properties(testcases: Sequence[ET.Element]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for case in testcases:
        for name, value in _properties_of(case).items():
            merged.setdefault(name, value)
    return merged


def _suite_properties(root: ET.Element, testcases: Sequence[ET.Element]) -> dict[str, str]:
    del testcases  # suite properties live on <testsuite>, not on the testcases
    props: dict[str, str] = {}
    for suite in root.iter():
        if _local_name(suite.tag) == "testsuite":
            for name, value in _properties_of(suite).items():
                props.setdefault(name, value)
    return props


def _final_response(case_props: Mapping[str, str], testcases: Sequence[ET.Element]) -> str | None:
    direct = case_props.get(FINAL_RESPONSE_PROPERTY)
    if direct:
        return direct
    for case in testcases:
        for child in case:
            if _local_name(child.tag) != "system-out" or not child.text:
                continue
            for line in child.text.splitlines():
                stripped = line.strip()
                if stripped.startswith(FINAL_RESPONSE_STDOUT_PREFIX):
                    value = stripped[len(FINAL_RESPONSE_STDOUT_PREFIX) :].strip()
                    if value:
                        return value
    return None


def _total_tokens(case_props: Mapping[str, str]) -> int | None:
    """Token total per the upstream rule (``junit_helper.py:154-158``), spec §6.3.3.

    Prefix-qualified keys win; ``usage_excluding_cached_inference.*`` is only used when no
    including-cached key exists, so a run is never double counted.  No key at all means ``None``
    (never 0).
    """
    prefix_keys = [key for key in case_props if key.endswith(TOKEN_SUFFIX) and key.startswith(TOKEN_PREFIX)]
    if prefix_keys:
        return _sum_ints(case_props[key] for key in prefix_keys)
    other_keys = [key for key in case_props if key.endswith(TOKEN_SUFFIX)]
    if other_keys:
        return _sum_ints(case_props[key] for key in other_keys)
    return None


def _sum_ints(values: Iterable[str]) -> int | None:
    total = 0
    seen = False
    for value in values:
        parsed = _as_int(value)
        if parsed is None:
            continue
        total += parsed
        seen = True
    return total if seen else None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Result rows (spec §8)
# ---------------------------------------------------------------------------------------------


def result_to_row(
    result: RunResult,
    *,
    method: str,
    flow: str,
    mutation: str,
    seed: int,
    started_at: str,
    finished_at: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Flatten a :class:`RunResult` into one ``results.jsonl`` row (spec §8)."""
    return {
        "run_id": result.run_id,
        "method": method,
        "flow": flow,
        "mutation": mutation,
        "seed": seed,
        "status": result.status,
        "passed": result.passed,
        "duration_s": result.duration_s,
        "cost_usd": result.cost_usd,
        "total_tokens": result.total_tokens,
        "junit_xml": result.junit_xml,
        "failure_message": result.failure_message,
        "started_at": started_at,
        "finished_at": finished_at,
        "model": model,
    }


def build_baseline_row(
    *,
    run_id: str,
    flow: str,
    mutation: str,
    seed: int,
    passed: bool,
    duration_s: float,
    failure_message: str | None,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """One baseline ``results.jsonl`` row: no cost/tokens/model/JUnit (spec §8)."""
    return {
        "run_id": run_id,
        "method": "baseline",
        "flow": flow,
        "mutation": mutation,
        "seed": seed,
        "status": STATUS_PASSED if passed else STATUS_FAILED,
        "passed": passed,
        "duration_s": duration_s,
        "cost_usd": None,
        "total_tokens": None,
        "junit_xml": None,
        "failure_message": failure_message,
        "started_at": started_at,
        "finished_at": finished_at,
        "model": None,
    }
