"""Sub-process orchestration CLI (spec §0-§7): ``record`` → ``generate`` → ``run`` → ``analyze``.

Entry point: ``uv run python -m record2gherkin <sub>``. The CLI is a thin shell by design — event
collection belongs to ``recorder.js``, distillation to the distiller, execution to the evaluation
runner and attribution to the attributor; this module only wires arguments to those seams and maps
their outcomes to exit codes (spec §1.2).

Key handling (spec §4): the API key is read into memory, travels to the child process through ``env=``
only, and never reaches ``argv``, a log line or a written artefact. Every message that may echo
external text (stdout tails, JUnit failure messages) passes through ``mask_secret`` before logging.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from record2gherkin.attributor import AttributionError, attribute_run
from record2gherkin.distiller import (
    DefaultPolisher,
    DistillError,
    distill_events,
    distill_file,
)
from record2gherkin.distiller.api import default_output_path
from record2gherkin.distiller.polisher import Polisher
from record2gherkin.evaluation.runner import (
    DEFAULT_TIMEOUT_S,
    LLM_KEY_PATH,
    REPO_ROOT,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_NO_JUNIT,
    STATUS_PASSED,
    STATUS_TIMEOUT,
    RunnerError,
    RunResult,
    build_run_plan,
    mask_secret,
    read_api_key,
    run_feature,
)
from testzeus_hercules.utils.logger import configure_logger, logger

# ---------------------------------------------------------------------------------------------
# Exit codes (spec §1.2)
# ---------------------------------------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_ATTRIBUTION = 3
EXIT_NO_JUNIT = 4
EXIT_INTERRUPTED = 130

#: ``RunResult.status`` -> exit code (spec §1.2); an unknown status falls back to :data:`EXIT_USAGE`.
EXIT_BY_STATUS: dict[str, int] = {
    STATUS_PASSED: EXIT_OK,
    STATUS_FAILED: EXIT_FAILED,
    STATUS_TIMEOUT: EXIT_ATTRIBUTION,
    STATUS_NO_JUNIT: EXIT_NO_JUNIT,
    STATUS_DRY_RUN: EXIT_OK,
}

# ---------------------------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------------------------

RECORDER_DIR = Path(__file__).resolve().parent / "recorder"
RECORDER_SOURCE_PATH = RECORDER_DIR / "recorder.js"
BOOKMARKLET_PATH = RECORDER_DIR / "bookmarklet.txt"

#: Default output roots (spec §1.1/§5.2); both live under the git-ignored ``dev_runs/``.
RECORDINGS_DIR = REPO_ROOT / "dev_runs" / "recordings"
RUNS_DIR = REPO_ROOT / "dev_runs" / "cli_runs"

RECORD_GOTO_TIMEOUT_MS = 30_000
RECORD_POLL_INTERVAL_S = 0.2
#: The initial navigate event is pushed by ``R2GRecorder.start()``; anything above that is user input.
EMPTY_RECORDING_MAX_EVENTS = 1

#: ``{{TEST_DATA:password_<seq>}}`` placeholders written for masked values (distiller spec §2.3).
PLACEHOLDER_PATTERN = re.compile(r"\{\{TEST_DATA:([^{}]+)\}\}")

#: Env keys shown by ``run --dry-run`` (spec §5.4), via ``RunPlan.env_redacted()``.
DRY_RUN_ENV_KEYS = ("LLM_MODEL_NAME", "LLM_MODEL_BASE_URL", "HEADLESS", "ENABLE_TELEMETRY", "LLM_MODEL_API_KEY")
#: Stand-in key for ``--dry-run`` without a key file: nothing is executed, so there is no leak surface.
DRY_RUN_PLACEHOLDER_KEY = "DRYRUN-PLACEHOLDER"

#: ``failure_message`` is truncated to this many characters before logging (spec §5.3).
FAILURE_MESSAGE_LIMIT = 500
#: Scenario names are truncated to this many characters in the analyze summary table (spec §6.1).
SUMMARY_SCENARIO_LIMIT = 60


class _UsageError(Exception):
    """Input/usage error: logged by the caller and mapped to :data:`EXIT_USAGE` (spec §1.2)."""


def _fmt(value: Any) -> str:
    """``-`` for ``None``, ``str()`` otherwise (spec §5.3: missing metrics print as a dash)."""
    return "-" if value is None else str(value)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit]


# ---------------------------------------------------------------------------------------------
# record (spec §2)
# ---------------------------------------------------------------------------------------------


def record_events(
    url: str,
    out_path: Path,
    *,
    headless: bool = False,
    max_duration_s: float = 0.0,
    stop_when: Callable[[Any], bool] | None = None,
) -> int:
    """Record a browser session of ``url`` into ``out_path`` and return an exit code (spec §2.1).

    The browser is driven with the synchronous Playwright API on the main thread; ``SIGINT`` only sets
    a :class:`threading.Event`, never calling into Playwright from the handler. ``stop_when`` is polled
    every :data:`RECORD_POLL_INTERVAL_S` seconds with the live ``page`` as its argument; the production
    predicate (SIGINT / ``--max-duration``) ignores it, test closures use it to drive real interactions.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a hard dependency of the project
        logger.error("error: playwright is not installed [%s]", exc)
        return EXIT_USAGE

    out_file = Path(out_path)
    sigint_requested = threading.Event()
    sigint_count = [0]
    started_at = time.monotonic()

    def _on_sigint(signum: int, frame: Any) -> None:
        del signum, frame
        sigint_count[0] += 1
        if sigint_count[0] >= 2:
            raise SystemExit(EXIT_INTERRUPTED)
        sigint_requested.set()
        logger.warning("record: 收到 Ctrl+C，正在停止录制并保存（再次 Ctrl+C 将强制中止且不保存）")

    previous_handler = signal.signal(signal.SIGINT, _on_sigint)
    predicate = stop_when if stop_when is not None else _builtin_stop_when(sigint_requested, max_duration_s, started_at)
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=headless)
            except Exception as exc:
                logger.error("error: cannot launch chromium [%s: %s]", type(exc).__name__, exc)
                return EXIT_USAGE
            try:
                return _record_session(browser, url, out_file, predicate)
            finally:
                browser.close()
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def _builtin_stop_when(sigint_requested: threading.Event, max_duration_s: float, started_at: float) -> Callable[[Any], bool]:
    """Production poll predicate: Ctrl+C or ``--max-duration`` expiry; the ``page`` argument is ignored."""

    def should_stop(page: Any) -> bool:
        del page
        if sigint_requested.is_set():
            return True
        return max_duration_s > 0 and (time.monotonic() - started_at) >= max_duration_s

    return should_stop


def _record_session(browser: Any, url: str, out_file: Path, predicate: Callable[[Any], bool]) -> int:
    """Open the page, inject ``recorder.js``, poll until stopped and persist the event stream."""
    try:
        page = browser.new_page()
        page.goto(url, timeout=RECORD_GOTO_TIMEOUT_MS)
    except Exception as exc:
        logger.error("error: cannot open %s [%s: %s]", url, type(exc).__name__, exc)
        return EXIT_USAGE

    try:
        page.evaluate(RECORDER_SOURCE_PATH.read_text(encoding="utf-8"))
        started = page.evaluate("R2GRecorder.start()")
    except OSError as exc:
        logger.error("error: cannot read %s [%s]", RECORDER_SOURCE_PATH, exc)
        return EXIT_USAGE
    except Exception as exc:
        logger.error("error: cannot inject recorder.js into %s [%s: %s]", url, type(exc).__name__, exc)
        return EXIT_USAGE
    if started is not True:
        logger.error("error: R2GRecorder.start() did not start a fresh recording on %s", url)
        return EXIT_USAGE

    logger.info("record: 录制中（%s）……操作完成后按 Ctrl+C 结束", url)
    stopped = _poll_until_stopped(page, predicate)
    if stopped != EXIT_OK:
        return stopped
    return _save_recording(page, out_file)


def _poll_until_stopped(page: Any, predicate: Callable[[Any], bool]) -> int:
    """Poll ``predicate(page)`` until it returns ``True``; abort on a closed page or a lost recorder."""
    while not predicate(page):
        if page.is_closed():
            logger.error("error: 页面已关闭（浏览器窗口被关或崩溃），本次录制不保存")
            return EXIT_USAGE
        try:
            page.evaluate("R2GRecorder.status()")
        except Exception as exc:
            logger.error(
                "error: 录制器已丢失（跨文档导航会随文档销毁注入的 JS，recorder spec §4.6.3 已知限制），本次录制不保存 [%s: %s]",
                type(exc).__name__,
                exc,
            )
            return EXIT_USAGE
        time.sleep(RECORD_POLL_INTERVAL_S)
    return EXIT_OK


def _save_recording(page: Any, out_file: Path) -> int:
    """Stop the recorder, read the JSON back, validate the payload shape and write it to disk."""
    try:
        page.evaluate("R2GRecorder.stop()")
        raw = page.evaluate("R2GRecorder.getJSON()")
        payload = json.loads(raw)
    except Exception as exc:
        logger.error("error: cannot read the recording back [%s: %s]", type(exc).__name__, exc)
        return EXIT_USAGE
    if not isinstance(payload, Mapping) or "session" not in payload or "events" not in payload:
        logger.error("error: recorder payload is missing the session/events keys [%s]", type(payload).__name__)
        return EXIT_USAGE

    events = payload.get("events")
    event_count = len(events) if isinstance(events, list) else 0
    if event_count <= EMPTY_RECORDING_MAX_EVENTS:
        logger.warning("empty_recording: 只录到初始 navigate 事件（无任何用户交互），产物仍按合法事件流保存")

    try:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("error: cannot write events JSON %s [%s]", out_file, exc)
        return EXIT_USAGE

    session = payload.get("session")
    origin = session.get("origin") if isinstance(session, Mapping) else None
    logger.info("record: events=%s origin=%s output=%s", event_count, _fmt(origin), out_file.resolve())
    return EXIT_OK


def _stop_when_for(args: argparse.Namespace) -> Callable[[Any], bool] | None:
    """Poll predicate handed to :func:`record_events`; ``None`` keeps its built-in SIGINT/duration one.

    Test seam (spec §8 cases 5/6): monkeypatch this function to return a ``page -> bool`` closure and a
    test can drive real page interactions before the recording stops — the browser page handle is not
    reachable from the CLI arguments otherwise.
    """
    del args
    return None


def _log_manual_instructions() -> None:
    """``record --manual``: print how to record through the bookmarklet, without starting a browser."""
    logger.info("record --manual: bookmarklet 文件 = %s", BOOKMARKLET_PATH.resolve())
    logger.info("  1) 浏览器书签管理器新建书签，把该书签地址设为该文件的全文（单行 javascript: 串）")
    logger.info("  2) 打开目标页面，点击一次该书签 → recorder.js 注入并自动开始录制")
    logger.info("  3) 手动操作页面；完成后在 DevTools 控制台执行 R2GRecorder.copy()（等价于 copy(R2GRecorder.getJSON())）")
    logger.info("  4) 把剪贴板内容存成 .json 文件，再执行 generate 子命令")
    logger.warning("bookmarklet 只注入一次：重复执行会把录制器重置为全新实例，之前录到的事件会丢失")
    logger.info("备选（CSP 严格的站点）：DevTools > Sources > Snippets 新建片段，粘贴 recorder.js 原文后运行")


def _cmd_record(args: argparse.Namespace) -> int:
    if args.manual:
        _log_manual_instructions()
        return EXIT_OK
    if not args.url:
        logger.error("error: record 需要目标 url（或改用 --manual 只打印 bookmarklet 用法）")
        return EXIT_USAGE
    out_path = Path(args.out) if args.out else RECORDINGS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-events.json"
    return record_events(args.url, out_path, headless=args.headless, max_duration_s=args.max_duration, stop_when=_stop_when_for(args))


# ---------------------------------------------------------------------------------------------
# generate (spec §3)
# ---------------------------------------------------------------------------------------------


def _needed_placeholder_keys(skeleton_text: str) -> list[str]:
    """``{{TEST_DATA:<key>}}`` keys of a skeleton, deduplicated in first-seen order (spec §3.1 step 3)."""
    keys: list[str] = []
    for key in PLACEHOLDER_PATTERN.findall(skeleton_text):
        if key not in keys:
            keys.append(key)
    return keys


def _read_test_data(path: str) -> dict[str, str]:
    """Parse ``--test-data`` into ``key -> value``, mirroring the distiller's value contract (spec §3.1 step 4).

    Usable values are strings/numbers with a non-blank ``str()`` form; ``bool``/``null`` (and blank
    strings) count as *not provided* — the same rule the distiller applies in ``_clean_test_data_values``,
    so the CLI's ``filled`` count can never contradict the produced feature. Objects/arrays are a hard
    input error.
    """
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _UsageError(f"cannot read --test-data JSON {file_path} [{exc}]") from exc
    if not isinstance(raw, dict):
        raise _UsageError(f"--test-data must be a JSON object, got {type(raw).__name__} ({file_path})")
    bad_keys = sorted(str(key) for key, value in raw.items() if isinstance(value, (dict, list)))
    if bad_keys:
        raise _UsageError(f"--test-data values must be strings/numbers; object/array values: {', '.join(bad_keys)}")

    provided: dict[str, str] = {}
    for key, value in raw.items():
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (str, int, float)):
            continue
        text = str(value)
        if text.strip():
            provided[str(key)] = text
    return provided


def _make_polisher() -> Polisher:
    """Build the LLM polisher for ``--polish`` (test seam; the default is never called offline)."""
    return DefaultPolisher()


def _cmd_generate(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    try:
        payload = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("error: cannot read events JSON %s [%s]", events_path, exc)
        return EXIT_USAGE

    try:  # P0-2 step 1: model-free skeleton, the cheapest source of the needed placeholder key set
        skeleton = distill_events(payload, polisher=None, test_data_values=None)
    except DistillError as exc:
        logger.error("error: skeleton self-check failed for %s [%s]", events_path, exc)
        return EXIT_FAILED

    needed = _needed_placeholder_keys(skeleton.skeleton_text)
    provided: dict[str, str] = {}
    if args.test_data:
        try:
            provided = _read_test_data(args.test_data)
        except _UsageError as exc:
            logger.error("error: %s", exc)
            return EXIT_USAGE

    missing = [key for key in needed if key not in provided]
    unused = [key for key in provided if key not in needed]
    output_path = Path(args.out) if args.out else Path(default_output_path(str(events_path)))

    try:  # P0-2 step 2: the only polishing call, fed with needed ∩ provided so unused keys cannot leak in
        result = distill_file(
            str(events_path),
            output_path=str(output_path),
            polisher=_make_polisher() if args.polish else None,
            test_data_values={key: provided[key] for key in needed if key in provided},
        )
    except DistillError as exc:
        logger.error("error: cannot distill %s [%s]", events_path, exc)
        return EXIT_FAILED

    for warning in result.warnings:
        logger.warning("generate: %s", warning)
    for key in missing:
        logger.warning("missing_test_data:%s", key)
    for key in unused:
        logger.warning("unused_test_data:%s", key)
    if missing:
        logger.warning("以上键无值，feature 中保留 {{TEST_DATA:...}} 占位符（%s），run 前需人工填充后重跑 generate", ", ".join(missing))

    resolved_output = Path(result.output_path or output_path).resolve()
    logger.info(
        "generate: output=%s needed=%s filled=%s used_llm_polish=%s fallback_reason=%s",
        resolved_output,
        len(needed),
        len(needed) - len(missing),
        result.used_llm_polish,
        result.fallback_reason,
    )
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# run (spec §5)
# ---------------------------------------------------------------------------------------------


def _make_run_id(feature: Path, base_dir: Path) -> str:
    """``cli_<timestamp>_<feature stem>``, with ``_1``/``_2``… appended until the directory is new."""
    stamp = f"cli_{datetime.now():%Y%m%d-%H%M%S}_{feature.stem}"
    run_id = stamp
    counter = 0
    while (base_dir / run_id).exists():
        counter += 1
        run_id = f"{stamp}_{counter}"
    return run_id


def _load_api_key(key_file: Path, *, dry_run: bool) -> str | None:
    """Read the key; ``None`` means "usage error" (only the dry-run branch tolerates a missing key)."""
    try:
        key = read_api_key(key_file)
    except RunnerError as exc:
        if not dry_run:
            logger.error("error: %s", exc)
            return None
        logger.warning("run: %s；--dry-run 用占位串继续构建执行计划（不会执行，无泄露面）", exc)
        return DRY_RUN_PLACEHOLDER_KEY
    if not key.strip():
        if not dry_run:
            logger.error("error: LLM key file is empty: %s", key_file)
            return None
        logger.warning("run: LLM key file is empty (%s)；--dry-run 用占位串继续", key_file)
        return DRY_RUN_PLACEHOLDER_KEY
    return key


def _log_run_summary(result: RunResult, project_root: Path, key: str) -> None:
    """Per-run summary (spec §5.3); the JUnit-derived ``failure_message`` is masked again here."""
    logger.info(
        "run: status=%s passed=%s duration_s=%s cost_usd=%s total_tokens=%s",
        result.status,
        result.passed,
        _fmt(result.duration_s),
        _fmt(result.cost_usd),
        _fmt(result.total_tokens),
    )
    logger.info("run: run_dir=%s", Path(result.run_dir).resolve())
    if result.junit_xml:
        junit_xml = Path(result.junit_xml).resolve()
        logger.info("run: junit_xml=%s", junit_xml)
        html_report = junit_xml.with_suffix(".html")
        if html_report.is_file():
            logger.info("run: html_report=%s", html_report)
    proofs = project_root / "proofs"
    if proofs.is_dir():
        logger.info("run: proofs=%s", proofs)
    log_files = project_root / "log_files"
    if log_files.is_dir():
        logger.info("run: log_files=%s", log_files)
    if result.failure_message:
        masked = mask_secret(result.failure_message, key)
        logger.warning("run: failure_message=%s", _clip(masked, FAILURE_MESSAGE_LIMIT))
    logger.info("run: 下一步 → uv run python -m record2gherkin analyze %s", result.run_dir)


def _cmd_run(args: argparse.Namespace) -> int:
    feature = Path(args.feature)
    if args.out_dir:
        run_dir = Path(args.out_dir).resolve()
        run_id = _make_run_id(feature, run_dir.parent)
    else:
        run_dir = RUNS_DIR / _make_run_id(feature, RUNS_DIR)
        run_id = run_dir.name
    project_root = (run_dir / "opt").resolve()

    key = _load_api_key(Path(args.key_file), dry_run=args.dry_run)
    if key is None:
        return EXIT_USAGE

    if args.dry_run:  # spec §5.4: assemble the plan, print it, execute nothing
        try:
            plan = build_run_plan(feature, project_root, api_key=key)
        except RunnerError as exc:
            logger.error("error: %s", exc)
            return EXIT_USAGE
        logger.info("run: dry-run run_id=%s", run_id)
        logger.info("run: project_root=%s", plan.project_root)
        logger.info("run: cmd=%s", " ".join(plan.cmd))
        env = plan.env_redacted()
        for name in DRY_RUN_ENV_KEYS:
            logger.info("run: env %s=%s", name, env.get(name, "-"))
        return EXIT_OK

    try:
        result = run_feature(feature, run_id=run_id, project_root=project_root, timeout_s=args.timeout, api_key=key)
    except KeyboardInterrupt:  # spec §5.5: the child lives in its own process group and keeps running
        logger.warning("run: 已中断（Ctrl+C）：Hercules 子进程可能仍在后台运行，请等待其自然结束或手动 pkill")
        return EXIT_INTERRUPTED
    except RunnerError as exc:
        logger.error("error: %s", exc)
        return EXIT_USAGE

    _log_run_summary(result, project_root, key)
    exit_code = EXIT_BY_STATUS.get(result.status)
    if exit_code is None:
        logger.warning("run: 未知的运行状态 %s，按用法错误处理", result.status)
        return EXIT_USAGE
    return exit_code


# ---------------------------------------------------------------------------------------------
# analyze (spec §6)
# ---------------------------------------------------------------------------------------------


def _make_analyzer() -> Any:
    """Build the LLM analyzer for ``--llm`` (test seam); raises when it cannot be constructed."""
    try:
        from record2gherkin.attributor import DefaultAttributorAnalyzer
    except ImportError as exc:  # pragma: no cover - the re-export is part of the package surface
        raise AttributionError(f"llm analyzer import failed: {exc}") from exc
    return DefaultAttributorAnalyzer()


def _result_json(result: Any) -> str:
    """``AttributionResult.to_json()`` in either shape: a JSON string is written verbatim (spec §6.1)."""
    value = result.to_json()
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _cmd_analyze(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        logger.error("error: run dir not found: %s", run_dir)
        return EXIT_USAGE

    analyzer = None
    if args.llm:  # spec §6.1/§6.4: an unavailable LLM layer degrades to the deterministic rules
        try:
            analyzer = _make_analyzer()
            logger.info("analyze: LLM 归因层已启用")
        except Exception as exc:
            logger.warning("analyze: llm analyzer unavailable (%s: %s)，降级纯规则继续", type(exc).__name__, exc)

    try:
        results = attribute_run(str(run_dir), junit_path=args.junit, analyzer=analyzer)
    except AttributionError as exc:
        logger.error("error: %s", exc)
        return EXIT_ATTRIBUTION
    if not results:
        logger.info("analyze: no failing testcases found; nothing to analyze")
        return EXIT_OK

    out_dir = Path(args.out_dir) if args.out_dir else run_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, result in enumerate(results, start=1):
        (out_dir / f"case-{index}.json").write_text(_result_json(result), encoding="utf-8")
        (out_dir / f"case-{index}.md").write_text(result.to_markdown(), encoding="utf-8")
        logger.info(
            "case-%s | %s | %s | conf=%.2f | by=%s | sig=%s",
            index,
            _clip(result.scenario, SUMMARY_SCENARIO_LIMIT),
            result.category,
            result.confidence,
            result.decided_by,
            result.rule_signature or "-",
        )
    logger.info("analyze: %s report(s) written to %s", len(results), out_dir.resolve())
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# Parser / entry point (spec §1.1)
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m record2gherkin", description="录制 → 蒸馏 → 执行 → 归因 四命令编排 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{record,generate,run,analyze}")

    record = subparsers.add_parser("record", help="启动浏览器注入 recorder.js 录制操作事件（默认有头）")
    record.add_argument("url", nargs="?", default=None, help="目标页 URL（http/https/file:// 均可；--manual 时忽略）")
    record.add_argument("--manual", action="store_true", help="不启浏览器，只打印 bookmarklet 用法")
    record.add_argument("--out", default=None, metavar="PATH", help="事件 JSON 落盘路径（默认 dev_runs/recordings/<时间戳>-events.json）")
    record.add_argument("--headless", action="store_true", help="无头模式（测试/CI 用；默认有头便于手动操作）")
    record.add_argument("--max-duration", type=float, default=0.0, metavar="SECONDS", help="到时自动结束并保存（0 = 不限时）")
    record.set_defaults(handler=_cmd_record)

    generate = subparsers.add_parser("generate", help="把事件 JSON 蒸馏为 feature（--test-data 填充 {{TEST_DATA:...}} 占位符）")
    generate.add_argument("events", help="事件 JSON 文件路径")
    generate.add_argument("--out", default=None, metavar="PATH", help="feature 落盘路径（默认事件文件同目录 <stem>.feature）")
    generate.add_argument("--polish", action="store_true", help="启用 LLM 润色（默认纯模板，零模型）")
    generate.add_argument("--test-data", default=None, metavar="PATH", help='JSON 文件：{"password_<seq>": "真实值", ...}')
    generate.set_defaults(handler=_cmd_generate)

    run = subparsers.add_parser("run", help="用 Hercules 子进程执行 feature 并汇总 JUnit 产物")
    run.add_argument("feature", help="feature 文件路径（恰 1 Feature 1 Scenario，generate 产物形态）")
    run.add_argument("--out-dir", default=None, metavar="PATH", help="本次运行根目录（其下 opt/ 为 Hercules 项目根；默认 dev_runs/cli_runs/<run_id>）")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, metavar="SECONDS", help=f"子进程超时（默认 {DEFAULT_TIMEOUT_S}s）")
    run.add_argument("--key-file", default=str(LLM_KEY_PATH), metavar="PATH", help="LLM key 文件（默认仓库根 LLM-Key.txt；key 只进子进程 env）")
    run.add_argument("--dry-run", action="store_true", help="只构建并打印执行计划（脱敏 env），不执行、不需真实 key")
    run.set_defaults(handler=_cmd_run)

    analyze = subparsers.add_parser("analyze", help="对一次 run 的产物目录做失败归因（默认纯规则，--llm 增强）")
    analyze.add_argument("run_dir", help="run 产物根目录（含 output/，即 run 命令打印的 run_dir）")
    analyze.add_argument("--junit", default=None, metavar="PATH", help="JUnit XML 路径（默认 glob run_dir/**/*.xml 取最新）")
    analyze.add_argument("--llm", action="store_true", help="启用 LLM 归因层（构造失败自动降级纯规则）")
    analyze.add_argument("--out-dir", default=None, metavar="PATH", help="报告落盘目录（默认 <run_dir>/analysis）")
    analyze.set_defaults(handler=_cmd_analyze)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: ``configure_logger`` first, then parse and dispatch (spec §0/§7)."""
    configure_logger("INFO")
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse --help (0) and usage errors (2) keep their own codes
        return int(exc.code or 0)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        logger.warning("interrupted by Ctrl+C")
        return EXIT_INTERRUPTED
    except Exception as exc:  # spec §7 fallback: no bare traceback, the type name aids debugging
        logger.error("error: unexpected %s [%s]", type(exc).__name__, exc)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - ``python -m record2gherkin.cli`` convenience
    raise SystemExit(main())
