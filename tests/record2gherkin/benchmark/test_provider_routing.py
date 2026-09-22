"""r2 provider 组：GLM（智谱 coding plan）provider 集成 —— 编排层（C1c 预检 / C5 路由 / manifest / CLI）。

红线：测试绝不读取真实 ``GLM-Key.txt``（key 读取一律 monkeypatch 或断言路径对象本身），不联网。
默认路径（不传 provider）与现状逐字节一致：默认 manifest 键集、默认 flags、默认预检模型
均与 r1 断言保持零变化（既有 F/T 组测试不改一字即为证）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark import preflight as preflight_module
from record2gherkin.benchmark.orchestrator import GLM_PLANNER_MODEL, Orchestrator
from record2gherkin.evaluation import runner as runner_module

FAKE_KEY = "sk-test-abcdefabcdefabcdef"
GLM_FAKE_KEY = "glm-fake-key-0123456789abcdef"
GLM_CODING_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"


# ---------------------------------------------------------------------------------------------
# provider 解析与角色路由默认
# ---------------------------------------------------------------------------------------------


def test_p_orchestrator_glm_routing_defaults(tasks: list[dict[str, Any]]) -> None:
    """glm：provider 配置就位、nav 默认 glm-5.3-flash、planner 默认 glm-5.3、helper 走 flash 档。"""
    view = Orchestrator("e", stage="pilot", tasks=tasks, dry_run=True, provider="glm")
    assert view.provider is runner_module.GLM
    assert view.provider.model == "glm-5.3-flash"
    assert view.provider.base_url == GLM_CODING_BASE_URL
    assert view.nav_model == "glm-5.3-flash"
    assert view.planner_model == GLM_PLANNER_MODEL == "glm-5.3"
    assert view.helper_model == "glm-5.3-flash"
    assert view.flags["nav_model"] == "glm-5.3-flash"  # flags 结构不变，仅 nav_model 取 provider 默认

    # 显式 --nav-model 覆盖 provider 默认
    overridden = Orchestrator("e", stage="pilot", tasks=tasks, dry_run=True, provider="glm", nav_model="glm-5.3")
    assert overridden.nav_model == "glm-5.3"


def test_p_orchestrator_default_provider_keeps_status_quo(tasks: list[dict[str, Any]]) -> None:
    """默认（None/deepseek）：nav/planner/helper 与 r1 常量逐项一致。"""
    for provider in (None, "deepseek"):
        view = Orchestrator("e", stage="pilot", tasks=tasks, dry_run=True, provider=provider)
        assert view.provider is runner_module.DEEPSEEK
        assert view.nav_model == orchestrator.DEFAULT_NAV_MODEL == "deepseek-flash"
        assert view.planner_model == orchestrator.ROLE_ROUTING_PLANNER_MODEL == runner_module.LLM_MODEL_NAME
        assert view.helper_model == orchestrator.ROLE_ROUTING_HELPER_MODEL


def test_p_role_routing_config_for_glm(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """C5 + glm：生成配置的端点=coding、planner=glm-5.3、nav/helper=glm-5.3-flash，且绝无 key。"""
    view = Orchestrator("miniwob-glm", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, role_routing=True, provider="glm")
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    config_path = view._prepare_role_routing()
    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    roles = loaded[orchestrator.ROLE_ROUTING_REF_KEY]
    assert roles["planner_agent"]["model_name"] == "glm-5.3"
    assert roles["nav_agent"]["model_name"] == "glm-5.3-flash"
    assert roles["helper_agent"]["model_name"] == "glm-5.3-flash"
    for entry in roles.values():
        assert entry["model_base_url"] == GLM_CODING_BASE_URL
        assert entry["model_api_type"] == "openai"
    assert "model_api_key" not in json.dumps(loaded).lower()
    assert "sk-" not in config_path.read_text(encoding="utf-8") and GLM_FAKE_KEY not in config_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# C1c 预检：glm 的端点与模型
# ---------------------------------------------------------------------------------------------


def test_p_preflight_probes_glm_endpoint_and_models(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """glm 预检：key 从 GLM key 路径读（只断言路径对象，不读真实内容）、探测 coding 端点 + flash 模型。"""
    read_paths: list[Path] = []

    def fake_read(path: Any = None) -> str:
        read_paths.append(Path(path))
        return GLM_FAKE_KEY

    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", fake_read)

    probed: list[tuple[str, str]] = []
    real_probe = preflight_module.probe_llm

    def spy(*, api_key: str, model: str | None = None, base_url: str | None = None, provider: Any = None, timeout_s: float = 30.0) -> Any:
        result = real_probe(api_key=api_key, model=model, base_url=base_url, provider=provider, timeout_s=timeout_s)
        config = runner_module.resolve_provider(provider)
        probed.append((model or config.model, base_url or config.base_url))
        return result

    monkeypatch.setattr(orchestrator.preflight_module, "probe_llm", spy)
    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", lambda self, *args, **kwargs: "ok")

    view = Orchestrator("e1", stage="pilot", exp_root=tmp_path / "a", tasks=tasks, provider="glm")
    assert view._run_preflight() == 0
    assert read_paths == [runner_module.GLM_KEY_PATH]  # 路径正确；真实文件内容从未被读取
    assert probed == [("glm-5.3-flash", GLM_CODING_BASE_URL)]  # nav 默认=provider 模型 → 只探一次

    probed.clear()
    routed = Orchestrator("e2", stage="pilot", exp_root=tmp_path / "b", tasks=tasks, role_routing=True, nav_model="glm-5.3", provider="glm")
    assert routed._run_preflight() == 0
    assert probed == [("glm-5.3-flash", GLM_CODING_BASE_URL), ("glm-5.3", GLM_CODING_BASE_URL)]


def test_p_probe_llm_provider_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """preflight.probe_llm 的 provider 参数：缺省 = deepseek 常量；glm → coding 端点 + flash 模型。"""
    captured: dict[str, Any] = {}
    real_init = preflight_module.ChatOpenAI.__init__

    def spy_init(self: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_init(self, **kwargs)

    monkeypatch.setattr(preflight_module.ChatOpenAI, "__init__", spy_init)
    monkeypatch.setattr(preflight_module.ChatOpenAI, "invoke", lambda self, *args, **kwargs: "ok")

    assert preflight_module.probe_llm(api_key=FAKE_KEY).ok is True
    assert captured["model"] == "deepseek-v4-pro" and captured["base_url"] == "https://api.deepseek.com"

    captured.clear()
    assert preflight_module.probe_llm(api_key=GLM_FAKE_KEY, provider="glm").ok is True
    assert captured["model"] == "glm-5.3-flash" and captured["base_url"] == GLM_CODING_BASE_URL

    captured.clear()
    assert preflight_module.probe_llm(api_key=FAKE_KEY, provider=runner_module.GLM, model="glm-5.3").ok is True
    assert captured["model"] == "glm-5.3" and captured["base_url"] == GLM_CODING_BASE_URL  # 显式模型覆盖 provider 默认


# ---------------------------------------------------------------------------------------------
# manifest 口径披露 + 结果行 + child env 透传
# ---------------------------------------------------------------------------------------------


def test_p_manifest_glm_adds_disclosure_default_unchanged(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """glm manifest 追加 llm_provider/model；deepseek manifest 键集与 r1 逐字节一致（无新键）。"""
    glm_view = Orchestrator("e-glm", stage="pilot", exp_root=tmp_path / "glm", tasks=tasks, provider="glm")
    glm_view.exp_dir.mkdir(parents=True, exist_ok=True)
    glm_view._write_manifest()
    glm_manifest = json.loads(glm_view.manifest_path.read_text(encoding="utf-8"))
    assert glm_manifest["llm_provider"] == "glm"
    assert glm_manifest["model"] == "glm-5.3-flash"
    assert glm_manifest["model_name"] == "glm-5.3-flash"
    assert glm_manifest["llm_base_url"] == GLM_CODING_BASE_URL

    default_view = Orchestrator("e-ds", stage="pilot", exp_root=tmp_path / "ds", tasks=tasks)
    default_view.exp_dir.mkdir(parents=True, exist_ok=True)
    default_view._write_manifest()
    default_manifest = json.loads(default_view.manifest_path.read_text(encoding="utf-8"))
    assert "llm_provider" not in default_manifest and "model" not in default_manifest
    assert default_manifest["model_name"] == "deepseek-v4-pro" and default_manifest["llm_base_url"] == "https://api.deepseek.com"


def test_p_execute_cell_glm_carries_provider_through(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """glm cell：run_feature 收到 provider、结果行 model=glm-5.3-flash；child extra env 键集不变。"""
    from record2gherkin.benchmark.orchestrator import plan_cells

    view = Orchestrator("miniwob-glm", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, provider="glm")
    view.results_path.parent.mkdir(parents=True, exist_ok=True)
    cell = plan_cells("miniwob-glm", "pilot", tasks=tasks)[0]

    captured: dict[str, Any] = {}

    def fake_run(feature_path: Path, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return orchestrator.runner_module.RunResult(
            run_id=cell.run_id, status="passed", passed=True, duration_s=1.0, cost_usd=None, total_tokens=None, junit_xml=None, failure_message=None, run_dir="x"
        )

    monkeypatch.setattr(orchestrator.runner_module, "run_feature", fake_run)
    monkeypatch.setattr(orchestrator, "fetch_reward", lambda *args, **kwargs: {"raw": 1, "done": True, "reason": ""})
    row = view._execute_cell(cell, goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert captured["provider"] is runner_module.GLM
    assert row["model"] == "glm-5.3-flash"
    assert view._child_extra_env() == {}  # glm 本身不新增 env 键（key/model/base_url 走 runner provider 通道）


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def test_p_cli_provider_glm_dry_run(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI ``--provider glm --dry-run``：正常出计划退出 0；dry-run 不读 key、不写文件。"""
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: GLM_FAKE_KEY)
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    code = orchestrator.main(["--exp-id", "miniwob-glm", "--stage", "pilot", "--dry-run", "--provider", "glm", "--exp-root", str(tmp_path / "dev_runs")])
    assert code == 0
    assert not (tmp_path / "dev_runs").exists()
    assert "orchestrator: miniwob." in caplog.text


def test_p_cli_invalid_provider_is_rejected(tmp_path: Path) -> None:
    """CLI 非法 provider 名 → argparse 拒绝（exit 2），不会落到 deepseek 静默兜底。"""
    with pytest.raises(SystemExit) as excinfo:
        orchestrator.main(["--exp-id", "e", "--stage", "pilot", "--provider", "gpt4", "--exp-root", str(tmp_path)])
    assert excinfo.value.code == 2
