"""r2 provider 组：GLM（智谱 coding plan）provider 集成 —— runner 层（KV key 文件 / provider 常量 / child env）。

红线：测试只用 tmp 假 key 文件与假 key，绝不读取真实 ``GLM-Key.txt`` / ``LLM-Key.txt``，不联网。
默认路径（不传 provider）与现状逐字节一致由本组 + 既有 E 组断言共同守卫。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from record2gherkin.evaluation import runner
from record2gherkin.evaluation.runner import (
    DEEPSEEK,
    GLM,
    LLMProviderConfig,
    RunnerError,
    build_child_env,
    build_run_plan,
    read_api_key,
    resolve_provider,
    run_feature,
)
from tests.record2gherkin.evaluation.conftest import FAKE_API_KEY

GLM_FAKE_KEY = "glm-fake-key-0123456789abcdef"
GLM_CODING_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"

#: GLM-Key.txt 的 KV 形态（假内容；含注释行与 BASE_URL/MODEL 行）
KV_TEXT = "# GLM-Key.txt — 智谱 coding plan 凭据（脱敏测试假文件）\n" "# 三行都要填。\n" "\n" f"KEY={GLM_FAKE_KEY}\n" f"BASE_URL={GLM_CODING_BASE_URL}\n" "MODEL=glm-5.3-flash\n"


def _write_kv(tmp_path: Path, text: str, name: str = "GLM-Key.txt") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# read_api_key：KV 解析 / 注释行 / 缺 MODEL 回退 / 单行向后兼容
# ---------------------------------------------------------------------------------------------


def test_p_kv_file_with_comments_parses_key_only(tmp_path: Path) -> None:
    """KV 文件（含 # 注释与空行）→ 只取 KEY= 的值；BASE_URL/MODEL 行不进 key。"""
    path = _write_kv(tmp_path, KV_TEXT)
    assert read_api_key(path) == GLM_FAKE_KEY


def test_p_kv_file_missing_model_still_parses_key(tmp_path: Path) -> None:
    """缺 MODEL（与 BASE_URL）行 → 回退到 provider 常量口径，key 解析不受影响。"""
    path = _write_kv(tmp_path, f"# only the key\nKEY={GLM_FAKE_KEY}\n")
    assert read_api_key(path) == GLM_FAKE_KEY
    # provider 常量自带 model/base_url 兜底（缺 MODEL 时运行时用 glm-5.3-flash + coding 端点）
    assert GLM.model == "glm-5.3-flash"
    assert GLM.base_url == GLM_CODING_BASE_URL


def test_p_kv_file_with_empty_key_is_a_clear_error(tmp_path: Path) -> None:
    """``KEY=`` 为空 → 明确报错（不悄悄跑空 key）。"""
    path = _write_kv(tmp_path, "KEY=\nBASE_URL=x\nMODEL=y\n")
    with pytest.raises(RunnerError, match="empty KEY"):
        read_api_key(path)


def test_p_single_line_file_keeps_legacy_behavior(tmp_path: Path) -> None:
    """单行 key 文件（deepseek 现状）→ 行为逐字节保留；缺文件仍报 RunnerError。"""
    path = _write_kv(tmp_path, f"  {FAKE_API_KEY}  \n", name="LLM-Key.txt")
    assert read_api_key(path) == FAKE_API_KEY
    with pytest.raises(RunnerError):
        read_api_key(tmp_path / "missing.txt")


# ---------------------------------------------------------------------------------------------
# resolve_provider / 常量
# ---------------------------------------------------------------------------------------------


def test_p_resolve_provider_default_glm_and_invalid() -> None:
    """None/deepseek → 现状常量打包；glm → GLM 配置；非法名 → RunnerError；配置对象原样透传。"""
    assert resolve_provider(None) is DEEPSEEK
    assert resolve_provider("deepseek") is DEEPSEEK
    assert resolve_provider(" GLM ") is GLM  # 大小写/空白不敏感
    assert DEEPSEEK == LLMProviderConfig(name="deepseek", key_path=runner.LLM_KEY_PATH, model="deepseek-v4-pro", base_url="https://api.deepseek.com")
    assert GLM == LLMProviderConfig(name="glm", key_path=runner.GLM_KEY_PATH, model="glm-5.3-flash", base_url=GLM_CODING_BASE_URL)
    custom = LLMProviderConfig(name="x", key_path=Path("/tmp/k"), model="m", base_url="https://x")
    assert resolve_provider(custom) is custom
    with pytest.raises(RunnerError, match="unknown LLM provider"):
        resolve_provider("gpt4")


def test_p_build_child_env_overrides_model_and_base_url() -> None:
    """build_child_env 可选覆盖生效：缺省 = deepseek 常量；覆盖 = GLM 常量；API_TYPE 恒 openai。"""
    default_env = build_child_env(FAKE_API_KEY)
    assert default_env["LLM_MODEL_NAME"] == "deepseek-v4-pro"
    assert default_env["LLM_MODEL_BASE_URL"] == "https://api.deepseek.com"
    assert default_env["LLM_MODEL_API_TYPE"] == "openai"

    glm_env = build_child_env(GLM_FAKE_KEY, model=GLM.model, base_url=GLM.base_url)
    assert glm_env["LLM_MODEL_NAME"] == "glm-5.3-flash"
    assert glm_env["LLM_MODEL_BASE_URL"] == GLM_CODING_BASE_URL
    assert glm_env["LLM_MODEL_API_TYPE"] == "openai"  # GLM 端点同为 OpenAI 兼容
    assert glm_env["LLM_MODEL_API_KEY"] == GLM_FAKE_KEY
    with pytest.raises(RunnerError):
        build_child_env("   ", model=GLM.model, base_url=GLM.base_url)


# ---------------------------------------------------------------------------------------------
# build_run_plan / run_feature：provider 透传（dry-run，不执行）
# ---------------------------------------------------------------------------------------------


def test_p_build_run_plan_default_env_unchanged(sample_feature: Path, tmp_run_root: Path) -> None:  # noqa: F811
    """默认调用（不传 provider）→ child env 与现状常量逐项一致。"""
    plan = build_run_plan(sample_feature, tmp_run_root, api_key=FAKE_API_KEY)
    assert plan.env["LLM_MODEL_NAME"] == "deepseek-v4-pro"
    assert plan.env["LLM_MODEL_BASE_URL"] == "https://api.deepseek.com"
    assert plan.env["LLM_MODEL_API_TYPE"] == "openai"
    assert plan.env["LLM_MODEL_API_KEY"] == FAKE_API_KEY


def test_p_dry_run_env_for_provider_glm(sample_feature: Path, tmp_run_root: Path) -> None:  # noqa: F811
    """``--provider glm`` 口径：dry-run env 四键齐全，model=glm-5.3-flash、base_url=coding 端点。"""
    plan = build_run_plan(sample_feature, tmp_run_root, api_key=GLM_FAKE_KEY, provider="glm")
    assert plan.env["LLM_MODEL_NAME"] == "glm-5.3-flash"
    assert plan.env["LLM_MODEL_BASE_URL"] == GLM_CODING_BASE_URL
    assert plan.env["LLM_MODEL_API_TYPE"] == "openai"
    assert plan.env["LLM_MODEL_API_KEY"] == GLM_FAKE_KEY
    assert GLM_FAKE_KEY not in str(plan.cmd)  # key 仍只走 env，绝不进 argv
    # 脱敏面不泄漏明文 key
    assert GLM_FAKE_KEY not in str(plan.env_redacted())
    assert plan.env_redacted()["LLM_MODEL_API_KEY"] == runner.REDACTED

    # 显式 LLMProviderConfig 与名字字符串等价
    plan_obj = build_run_plan(sample_feature, tmp_run_root, api_key=GLM_FAKE_KEY, provider=GLM)
    assert dict(plan_obj.env) == dict(plan.env)


def test_p_run_feature_glm_reads_key_from_provider_key_path(sample_feature: Path, tmp_path: Path) -> None:  # noqa: F811
    """provider 自带 key 文件：tmp KV 假文件供 key（绝不触碰真实 GLM-Key.txt），dry-run 不执行。"""
    key_path = _write_kv(tmp_path, KV_TEXT, name="GLM-Key-copy.txt")
    custom = LLMProviderConfig(name="glm-test", key_path=key_path, model="glm-5.3-flash", base_url=GLM_CODING_BASE_URL)
    project_root = tmp_path / "runs" / "glm__F1__M0__s1" / "opt"
    result = run_feature(sample_feature, run_id="glm__F1__M0__s1", project_root=project_root, provider=custom, dry_run=True)
    assert result.status == runner.STATUS_DRY_RUN
    assert result.passed is False and result.junit_xml is None
