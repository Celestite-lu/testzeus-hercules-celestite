"""C 组（JUnit 解析）+ E 组（执行器 dry 路径）：spec §9 C16-C21 / E26-E29。

真 LLM 一律不跑：E 组把子进程接缝（``build_run_plan``）monkeypatch 掉，超时用例只跑一个
``sleep`` 子进程。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.evaluation import runner
from record2gherkin.evaluation.metrics import ROW_KEYS
from record2gherkin.evaluation.runner import (
    COST_KEY,
    STATUS_DRY_RUN,
    STATUS_TIMEOUT,
    JUnitParseError,
    RunPlan,
    build_child_env,
    build_command,
    build_run_plan,
    junit_path_for,
    mask_secret,
    parse_junit_xml,
    run_feature,
)
from tests.record2gherkin.evaluation.conftest import (
    FAKE_API_KEY,
    build_junit_xml,
    cost_properties,
    write_junit,
)

# ---------------------------------------------------------------------------------------------
# C 组：JUnit 解析
# ---------------------------------------------------------------------------------------------


def test_c16_passing_case_with_cost_and_tokens(tmp_path: Path) -> None:
    """C16 通过用例（无 failure，含 cost/token 属性）→ passed=True 且数值正确。"""
    xml_text = build_junit_xml(
        [
            {
                "name": "MiniShop 商城",
                "classname": "Recorded flow",
                "time": "42.5",
                "properties": cost_properties(cost="0.0123", tokens="12345"),
                "system_out": ["Final Response: all good"],
            }
        ],
        suite_properties={"total_execution_cost": "0.0123", "total_token_used": "12345"},
    )
    parsed = parse_junit_xml(write_junit(tmp_path, xml_text))
    assert parsed["passed"] is True
    assert parsed["terminate"] == "yes"
    assert parsed["failure_message"] is None
    assert parsed["final_response"] == "all good"
    assert parsed["duration_s"] == pytest.approx(42.5)
    assert parsed["cost_usd"] == pytest.approx(0.0123)
    assert parsed["total_tokens"] == 12345
    assert parsed["testcase_count"] == 1


def test_c17_failing_case_extracts_message(tmp_path: Path) -> None:
    """C17 失败用例（含 ``<failure>`` 与 message）→ passed=False 且 message 提取。"""
    xml_text = build_junit_xml(
        [
            {
                "name": "MiniShop 商城",
                "time": "10",
                "properties": cost_properties(),
                "failure": {"message": "Expected text &#39;找到 1 件商品&#39; not visible", "text": "stack"},
            }
        ]
    )
    parsed = parse_junit_xml(write_junit(tmp_path, xml_text))
    assert parsed["passed"] is False
    assert "找到 1 件商品" in parsed["failure_message"]
    assert parsed["cost_usd"] == pytest.approx(0.0123)  # 失败运行的成本同样入账（失败数据不丢口径）


def test_c18_cost_falls_back_to_suite_property_with_str_conversion(tmp_path: Path) -> None:
    """C18 无 cost 属性 → cost_usd=None（非 0）；suite 级 total_execution_cost 兜底（字符串 float()/int() 转换）。"""
    xml_text = build_junit_xml(
        [{"name": "case", "time": "3", "properties": {"Terminate": "yes"}}],
        suite_properties={"total_execution_cost": "0.2500", "total_token_used": "999"},
    )
    parsed = parse_junit_xml(write_junit(tmp_path, xml_text))
    assert parsed["cost_usd"] == pytest.approx(0.25)
    assert parsed["total_tokens"] is None  # spec §6.3: token 无 testcase 键时为 None（不回落 suite）
    # 无法转换的 suite 值 → None
    xml_bad = build_junit_xml(
        [{"name": "case", "properties": {"Terminate": "yes"}}],
        suite_properties={"total_execution_cost": "not-a-number"},
    )
    assert parse_junit_xml(write_junit(tmp_path, xml_bad, name="bad.xml"))["cost_usd"] is None


def test_c19_malformed_xml_never_passes(tmp_path: Path) -> None:
    """C19 畸形 XML → 返回明确定义的 error 形态（不静默当通过）。"""
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><testsuite><testcase", encoding="utf-8")
    with pytest.raises(JUnitParseError):
        parse_junit_xml(broken)
    empty = write_junit(tmp_path, '<?xml version="1.0"?><testsuites/>', name="empty.xml")
    with pytest.raises(JUnitParseError):
        parse_junit_xml(empty)
    with pytest.raises(JUnitParseError):
        parse_junit_xml(tmp_path / "missing.xml")


def test_c20_multiple_testcases_are_defensive_all(tmp_path: Path) -> None:
    """C20 多 testcase（防御，正常不出现）→ passed=all 且 testcase_count>1。"""
    xml_text = build_junit_xml(
        [
            {"name": "ok", "time": "1", "properties": cost_properties(tokens="10", exclude_tokens="10")},
            {"name": "bad", "time": "2", "properties": {}, "failure": {"message": "boom"}},
        ]
    )
    parsed = parse_junit_xml(write_junit(tmp_path, xml_text))
    assert parsed["passed"] is False
    assert parsed["testcase_count"] == 2
    assert parsed["duration_s"] == pytest.approx(3.0)
    assert parsed["failure_message"] == "boom"

    all_ok = parse_junit_xml(
        write_junit(
            tmp_path,
            build_junit_xml([{"name": "a", "time": "1", "properties": {}}, {"name": "b", "time": "1", "properties": {}}]),
            name="two_ok.xml",
        )
    )
    assert all_ok["passed"] is True and all_ok["testcase_count"] == 2


def test_c21_token_key_selection_never_double_counts(tmp_path: Path) -> None:
    """C21 token 键选取：incl+excl 同时存在时只取 incl（多键求和、不双计）；仅 excl 时回落；皆无 → None。"""
    both = build_junit_xml(
        [
            {
                "name": "case",
                "properties": {
                    "usage_including_cached_inference.deepseek-chat.total_tokens": "1000",
                    "usage_including_cached_inference.other-model.total_tokens": "500",
                    "usage_excluding_cached_inference.deepseek-chat.total_tokens": "1000",
                },
            }
        ]
    )
    assert parse_junit_xml(write_junit(tmp_path, both, name="both.xml"))["total_tokens"] == 1500

    only_excl = build_junit_xml([{"name": "case", "properties": {"usage_excluding_cached_inference.deepseek-chat.total_tokens": "777"}}])
    assert parse_junit_xml(write_junit(tmp_path, only_excl, name="excl.xml"))["total_tokens"] == 777

    none = build_junit_xml([{"name": "case", "properties": {"Terminate": "yes"}}])
    assert parse_junit_xml(write_junit(tmp_path, none, name="none.xml"))["total_tokens"] is None


def test_c21b_cost_key_is_the_flattened_including_key(tmp_path: Path) -> None:
    """C21 补充：唯一取数键 ``usage_including_cached_inference.total_cost``（模型分列键不参与）。"""
    xml_text = build_junit_xml(
        [
            {
                "name": "case",
                "properties": {COST_KEY: "0.5", "usage_including_cached_inference.deepseek-chat.total_cost": "0.2"},
            }
        ]
    )
    assert parse_junit_xml(write_junit(tmp_path, xml_text, name="cost.xml"))["cost_usd"] == pytest.approx(0.5)
    xml_model_only = build_junit_xml([{"name": "case", "properties": {"usage_including_cached_inference.deepseek-chat.total_cost": "0.2"}}])
    assert parse_junit_xml(write_junit(tmp_path, xml_model_only, name="cost2.xml"))["cost_usd"] is None


# ---------------------------------------------------------------------------------------------
# E 组：执行器（dry 路径）
# ---------------------------------------------------------------------------------------------


def test_e26_command_uses_absolute_paths_and_never_carries_the_key(sample_feature: Path, tmp_run_root: Path) -> None:
    """E26 cmd 含 ``--input-file/--project-base/--output-path`` 三参数且为绝对路径；key 不在 cmd。"""
    plan = build_run_plan(sample_feature, tmp_run_root, api_key=FAKE_API_KEY)
    cmd = list(plan.cmd)
    for flag in ("--input-file", "--project-base", "--output-path"):
        assert flag in cmd, f"missing {flag}"
        value = cmd[cmd.index(flag) + 1]
        assert Path(value).is_absolute(), f"{flag} must be absolute: {value}"
    assert cmd[cmd.index("--input-file") + 1] == str(sample_feature.resolve())
    assert cmd[cmd.index("--project-base") + 1] == str(plan.project_root)
    assert cmd[cmd.index("--output-path") + 1] == str(plan.project_root / "output")
    assert plan.cwd == runner.REPO_ROOT
    for element in cmd:
        assert FAKE_API_KEY not in element
        for fragment_len in (8, 16):
            assert FAKE_API_KEY[:fragment_len] not in element
    assert plan.junit_path == junit_path_for(sample_feature, plan.project_root)
    assert plan.junit_path.name == "F3.feature_result.xml"
    assert (plan.project_root / "input").is_dir()


def test_e27_child_env_carries_the_spec_configuration(sample_feature: Path, tmp_run_root: Path) -> None:
    """E27 child env 含 §4.3 全部键；ENABLE_TELEMETRY=0；key 仅存在于 env 值。"""
    env = build_child_env(FAKE_API_KEY)
    assert env["LLM_MODEL_NAME"] == "openai/deepseek-chat"
    assert env["LLM_MODEL_BASE_URL"] == "https://api.deepseek.com"
    assert env["LLM_MODEL_API_TYPE"] == "openai"
    assert env["LLM_MODEL_API_KEY"] == FAKE_API_KEY
    assert env["ENABLE_TELEMETRY"] == "0"
    assert env["HEADLESS"] == "true"
    assert FAKE_API_KEY not in " ".join(env.keys())
    with pytest.raises(runner.RunnerError):
        build_child_env("   ")
    plan = build_run_plan(sample_feature, tmp_run_root, api_key=FAKE_API_KEY)
    assert plan.env["LLM_MODEL_API_KEY"] == FAKE_API_KEY
    assert FAKE_API_KEY not in str(plan.cmd)
    assert FAKE_API_KEY not in str(plan.feature_path) + str(plan.project_root)
    # env_redacted 不含明文（可安全写盘/进 manifest）
    assert FAKE_API_KEY not in str(plan.env_redacted())
    assert plan.env_redacted()["LLM_MODEL_API_KEY"] == runner.REDACTED


def test_e27b_dry_run_constructs_without_executing(sample_feature: Path, tmp_run_root: Path) -> None:
    """E27 补充：``--dry-run`` 只构造不执行（不产生 stdout.log、不启动进程）。"""
    result = run_feature(sample_feature, run_id="generated__F3__M0__s1", project_root=tmp_run_root, api_key=FAKE_API_KEY, dry_run=True)
    assert result.status == STATUS_DRY_RUN
    assert result.passed is False
    assert result.junit_xml is None and result.cost_usd is None and result.total_tokens is None
    assert result.run_dir == str(tmp_run_root.parent)
    assert not (tmp_run_root.parent / "stdout.log").exists()


def test_e28_mask_secret_removes_every_trace() -> None:
    """E28 ``mask_secret``：含 key 的文本替换后无残留；空 key 时原样返回。"""
    text = f"Authorization: Bearer {FAKE_API_KEY} -> 401; prefix {FAKE_API_KEY[:8]}"
    masked = mask_secret(text, FAKE_API_KEY)
    assert FAKE_API_KEY not in masked
    assert FAKE_API_KEY[:8] not in masked
    assert runner.REDACTED in masked
    assert mask_secret(text, "") == text
    assert mask_secret(text, "   ") == text
    assert mask_secret("", FAKE_API_KEY) == ""
    assert mask_secret("no secret here", FAKE_API_KEY) == "no secret here"


def test_e28b_masked_log_is_written_for_the_dry_seam(sample_feature: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E28 补充：子进程 stdout 先脱敏再落盘 ``<run_dir>/stdout.log``。"""
    run_dir = tmp_path / "runs" / "generated__F1__M0__s1"
    project_root = run_dir / "opt"
    project_root.mkdir(parents=True)
    fake_cmd = (sys.executable, "-c", f"print('key={FAKE_API_KEY}'); print('hello')")
    plan = RunPlan(
        feature_path=sample_feature,
        project_root=project_root,
        run_dir=run_dir,
        output_path=project_root / "output",
        junit_path=project_root / "output" / "F3.feature_result.xml",
        cmd=fake_cmd,
        cwd=tmp_path,
        env={"LLM_MODEL_API_KEY": FAKE_API_KEY},
    )
    monkeypatch.setattr(runner, "build_run_plan", lambda *args, **kwargs: plan)
    result = run_feature(sample_feature, run_id="generated__F1__M0__s1", project_root=project_root, timeout_s=30)
    assert result.status == runner.STATUS_NO_JUNIT  # 无 JUnit 产物 → 失败数据，不是静默通过
    log_text = (run_dir / "stdout.log").read_text(encoding="utf-8")
    assert "hello" in log_text
    assert FAKE_API_KEY not in log_text
    assert result.failure_message is not None and FAKE_API_KEY not in result.failure_message


def test_e29_timeout_kills_the_process(sample_feature: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E29 超时路径：以 0.5s timeout 跑 ``sleep 5`` 子进程 → status="timeout"。"""
    run_dir = tmp_path / "generated__F1__M0__s1"
    project_root = run_dir / "opt"
    project_root.mkdir(parents=True)
    plan = RunPlan(
        feature_path=sample_feature,
        project_root=project_root,
        run_dir=run_dir,
        output_path=project_root / "output",
        junit_path=project_root / "output" / "F3.feature_result.xml",
        cmd=(sys.executable, "-c", "import time; print('started'); time.sleep(30)"),
        cwd=tmp_path,
        env={},
    )
    monkeypatch.setattr(runner, "build_run_plan", lambda *args, **kwargs: plan)
    started = time.monotonic()
    result = run_feature(sample_feature, run_id="generated__F1__M0__s1", project_root=project_root, timeout_s=1)
    elapsed = time.monotonic() - started
    assert result.status == STATUS_TIMEOUT
    assert result.passed is False
    assert elapsed < 20, "timeout must kill the child instead of waiting for it"
    assert "timeout after 1s" in (result.failure_message or "")
    assert result.cost_usd is None and result.total_tokens is None
    assert (run_dir / "stdout.log").is_file()


def test_build_command_shape(sample_feature: Path, tmp_run_root: Path) -> None:
    """补充：命令模板与 spec §4.2 逐字一致（sys.executable -u -m testzeus_hercules ...）。"""
    cmd = build_command(sample_feature, tmp_run_root)
    assert cmd[:4] == (sys.executable, "-u", "-m", "testzeus_hercules")
    assert "--llm-model-api-key" not in cmd


def test_junit_discovery_covers_timestamp_subdir(sample_feature: Path, tmp_path: Path) -> None:
    """补充（上游实测）：JUnit 实际落在 ``<project-base>/output/run_<TS>/`` 时间戳子目录，必须能定位。"""
    project_root = tmp_path / "opt"
    runner.prepare_run_dir(project_root)
    assert (project_root / "input").is_dir() and (project_root / "output").is_dir()
    assert runner.find_junit_xml(sample_feature, project_root) is None
    direct = runner.junit_path_for(sample_feature, project_root)
    direct.write_text("<testsuites/>", encoding="utf-8")
    assert runner.find_junit_xml(sample_feature, project_root) == direct
    direct.unlink()
    nested = project_root / "output" / "run_20260921_024147"
    nested.mkdir(parents=True)
    artefact = nested / "F3.feature_result.xml"
    artefact.write_text(build_junit_xml([{"name": "case", "properties": cost_properties()}]), encoding="utf-8")
    assert runner.find_junit_xml(sample_feature, project_root) == artefact


def test_collect_result_reads_the_timestamp_subdir(sample_feature: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补充：端到端走一遍「子进程 → 时间戳子目录 JUnit → RunResult 数值」。"""
    run_dir = tmp_path / "generated__F3__M0__s1"
    project_root = runner.prepare_run_dir(run_dir / "opt")
    nested = project_root / "output" / "run_20260921_024147"
    nested.mkdir(parents=True)
    (nested / "F3.feature_result.xml").write_text(build_junit_xml([{"name": "case", "time": "9.5", "properties": cost_properties(cost="0.03", tokens="1234")}]), encoding="utf-8")
    plan = RunPlan(
        feature_path=sample_feature,
        project_root=project_root,
        run_dir=run_dir,
        output_path=project_root / "output",
        junit_path=runner.junit_path_for(sample_feature, project_root),
        cmd=(sys.executable, "-c", "print('noop')"),
        cwd=tmp_path,
        env={},
    )
    monkeypatch.setattr(runner, "build_run_plan", lambda *args, **kwargs: plan)
    result = run_feature(sample_feature, run_id="generated__F3__M0__s1", project_root=project_root, timeout_s=30)
    assert result.status == runner.STATUS_PASSED
    assert result.junit_xml == str(nested / "F3.feature_result.xml")
    assert result.cost_usd == pytest.approx(0.03) and result.total_tokens == 1234 and result.duration_s == pytest.approx(9.5)


def test_run_feature_reports_duration_and_row_shape(sample_feature: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补充：JUnit duration 优先、results.jsonl 行字段与 spec §8 一致。"""
    run_dir = tmp_path / "generated__F3__M0__s1"
    project_root = run_dir / "opt"
    output = project_root / "output"
    output.mkdir(parents=True)
    junit = output / "F3.feature_result.xml"
    junit.write_text(
        build_junit_xml([{"name": "case", "time": "7.5", "properties": cost_properties(cost="0.02", tokens="500")}]),
        encoding="utf-8",
    )
    plan = RunPlan(
        feature_path=sample_feature,
        project_root=project_root,
        run_dir=run_dir,
        output_path=output,
        junit_path=junit,
        cmd=(sys.executable, "-c", "print('noop')"),
        cwd=tmp_path,
        env={},
    )
    monkeypatch.setattr(runner, "build_run_plan", lambda *args, **kwargs: plan)
    result = run_feature(sample_feature, run_id="generated__F3__M0__s1", project_root=project_root, timeout_s=30)
    assert result.status == runner.STATUS_PASSED and result.passed is True
    assert result.duration_s == pytest.approx(7.5)
    assert result.cost_usd == pytest.approx(0.02)
    assert result.total_tokens == 500
    row = runner.result_to_row(result, method="generated", flow="F3", mutation="M0", seed=1, started_at="t0", finished_at="t1", model=runner.LLM_MODEL_NAME)
    assert set(row) == set(ROW_KEYS)
    assert row["junit_xml"] == str(junit)
    baseline_row = runner.build_baseline_row(run_id="baseline__F1__M0__s1", flow="F1", mutation="M0", seed=1, passed=False, duration_s=1.2, failure_message="boom", started_at="t0", finished_at="t1")
    assert baseline_row["cost_usd"] is None and baseline_row["junit_xml"] is None and baseline_row["model"] is None


def test_run_feature_missing_key_file_is_a_clear_error(sample_feature: Path, tmp_run_root: Path) -> None:
    """补充：key 文件缺失时给出明确错误（不悄悄跑空 key）。"""
    with pytest.raises(runner.RunnerError):
        runner.read_api_key(Path("/nonexistent/LLM-Key.txt"))
    with pytest.raises(runner.RunnerError):
        build_run_plan(sample_feature, tmp_run_root, api_key="")
