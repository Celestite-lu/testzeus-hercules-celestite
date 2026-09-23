"""T7 — C5 role-routing config generation and the four-key env injection (spec-r2 §6).

The red line under test: the generated ``agents_llm_config.json`` never contains a key
(``model_api_key`` omitted, no ``sk-`` anywhere); the key travels through the child env only, on
exactly the four keys of spec-r2 §6.2 (``AGENTS_LLM_CONFIG_FILE`` / ``AGENTS_LLM_CONFIG_FILE_REF_KEY``
/ ``MODEL_API_KEY`` / ``OPENAI_API_KEY`` — the last two share one value for the planner's bare
``ChatOpenAI``, review-r2 M1).  Flags off → no file, no keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark.orchestrator import (
    AGENTS_LLM_CONFIG_FILENAME,
    ROLE_ROUTING_REF_KEY,
    Orchestrator,
    build_agents_llm_config,
    role_routing_env,
    write_agents_llm_config,
)

FAKE_KEY = "sk-test-abcdefabcdefabcdef"


def _view(tasks: list[dict[str, Any]], tmp_path: Path, **overrides: Any) -> Orchestrator:
    return Orchestrator("miniwob-r2", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, **overrides)


def test_t7_generated_config_shape_and_no_key() -> None:
    """T7：三角色形态正确、模型名/端点一致、``llm_config_params`` 固定；绝无 key 字段。"""
    config = build_agents_llm_config(nav_model="deepseek-flash", base_url="https://api.deepseek.com", api_type="openai")
    assert set(config) == {ROLE_ROUTING_REF_KEY}
    roles = config[ROLE_ROUTING_REF_KEY]
    assert set(roles) == {"planner_agent", "nav_agent", "helper_agent"}
    assert roles["planner_agent"]["model_name"] == "deepseek-v4-pro"  # planner 保持 r1 基准模型
    assert roles["nav_agent"]["model_name"] == "deepseek-flash"  # nav 路由到 flash
    assert roles["helper_agent"]["model_name"] == "deepseek-v4-pro"
    for entry in roles.values():
        assert set(entry) == {"model_name", "model_base_url", "model_api_type", "llm_config_params"}
        assert entry["model_base_url"] == "https://api.deepseek.com"
        assert entry["model_api_type"] == "openai"
        assert entry["llm_config_params"] == {"temperature": 0.0, "cache_seed": None}
        assert "model_api_key" not in entry
    text = json.dumps(config)
    assert "sk-" not in text and "model_api_key" not in text.lower()


def test_t7_written_file_is_valid_json_without_secrets(tmp_path: Path) -> None:
    """T7：落盘文件是合法 JSON、不含 ``model_api_key``/``sk-``；写含 key 的配置被拒绝。"""
    config = build_agents_llm_config(nav_model="deepseek-flash", base_url="https://api.deepseek.com", api_type="openai")
    path = write_agents_llm_config(tmp_path / AGENTS_LLM_CONFIG_FILENAME, config)
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == config
    lowered = path.read_text(encoding="utf-8").lower()
    assert "model_api_key" not in lowered and "sk-" not in path.read_text(encoding="utf-8")

    poisoned = json.loads(json.dumps(config))
    poisoned[ROLE_ROUTING_REF_KEY]["nav_agent"]["model_api_key"] = FAKE_KEY
    with pytest.raises(orchestrator.BenchmarkError):
        write_agents_llm_config(tmp_path / "poison.json", poisoned)


def test_t7_role_routing_env_is_exactly_the_four_keys() -> None:
    """T7：env 注入恰含四键、后两者同值；空 key 拒绝注入。"""
    env = role_routing_env(Path("/exp/agents_llm_config.json"), f"  {FAKE_KEY}  ")
    assert set(env) == {"AGENTS_LLM_CONFIG_FILE", "AGENTS_LLM_CONFIG_FILE_REF_KEY", "MODEL_API_KEY", "OPENAI_API_KEY"}
    assert env["AGENTS_LLM_CONFIG_FILE"] == "/exp/agents_llm_config.json"
    assert env["AGENTS_LLM_CONFIG_FILE_REF_KEY"] == ROLE_ROUTING_REF_KEY
    assert env["MODEL_API_KEY"] == FAKE_KEY  # strip 后注入
    assert env["OPENAI_API_KEY"] == env["MODEL_API_KEY"]  # 同值（review-r2 M1）
    with pytest.raises(orchestrator.runner_module.RunnerError):
        role_routing_env(Path("/x"), "   ")


def test_t7_child_env_exact_key_set_with_flag_on_and_off(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T7：flag on → ``_child_extra_env`` 恰为四键；flag off → 不生成文件、不注入任何键。"""
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)
    view = _view(tasks, tmp_path, role_routing=True)
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    config_path = view._prepare_role_routing()
    assert config_path == view.exp_dir / AGENTS_LLM_CONFIG_FILENAME and config_path.is_file()
    view._routing_config_path = config_path
    env = view._child_extra_env()
    assert env == role_routing_env(config_path, FAKE_KEY)
    assert set(env) == {"AGENTS_LLM_CONFIG_FILE", "AGENTS_LLM_CONFIG_FILE_REF_KEY", "MODEL_API_KEY", "OPENAI_API_KEY"}

    off = _view(tasks, tmp_path / "off")
    assert off._child_extra_env() == {}
    assert not (off.exp_dir / AGENTS_LLM_CONFIG_FILENAME).exists()
    assert not off.exp_dir.exists()  # flag off 时连目录都不建


def test_t7_latency_and_extra_tools_merge_over_routing(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """T7 补充：多 flag 合并 —— latency 五键 + LOAD_EXTRA_TOOLS + 路由四键同时到位。

    r3 适配（spec-r3 §5.1/T10）：``--extra-tools`` 现默认同时注入 ``EXTRA_TOOLS_MODULES`` 子集
    （默认 ``drag_and_drop_tool``），键数 5+1+4 → 5+2+4。
    """
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)
    view = _view(tasks, tmp_path, role_routing=True, latency_env=True, extra_tools=True)
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    view._routing_config_path = view._prepare_role_routing()
    env = view._child_extra_env()
    assert env["LOAD_EXTRA_TOOLS"] == "true"
    assert env["EXTRA_TOOLS_MODULES"] == "drag_and_drop_tool"
    for key, value in orchestrator.LATENCY_ENV_OVERRIDES.items():
        assert env[key] == value
    assert env["AGENTS_LLM_CONFIG_FILE"] == str(view.exp_dir / AGENTS_LLM_CONFIG_FILENAME)
    assert len(env) == 5 + 2 + 4
