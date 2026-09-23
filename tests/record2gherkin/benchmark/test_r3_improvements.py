"""r3 离线单测 T1-T12（spec-r3 §8）。

全部不跑真 LLM、零真实网络；env 用 monkeypatch 隔离，config 单例键用 dict 直改 + 清理（与
test_engine_guards.py 同法）。覆盖：

- T1  R3-1 nav 补全上限（``NAV_MAX_COMPLETION_TOKENS`` env>0 无条件覆盖 adapt 注入的 4096）
- T2  R3-2 planner 超时分档（wait_for 层 + provider 层同源）
- T3  E1 extra_tools 子集加载（subprocess 隔离 config 单例）
- T4  E2 文件工具调用日志行
- T5  E3 open_url scheme 白名单
- T6  E5 沙箱关停
- T7  扫描标记（file-tool / open-url-blocked / sandbox-disabled）
- T8  E4 metrics clean 口径 + invalid_cells 截断
- T9  R3-3 drag 选择器解析
- T10 orchestrator 注入与 flags
- T11 R3-6 断言纪律开关
- T12 R3-4 D 臂 dry-run（零代码组合验证）
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from record2gherkin.benchmark import metrics as metrics_module
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark.metrics import (
    clean_rate,
    invalid_cell_list,
    latest_rows,
    summarize,
)
from record2gherkin.benchmark.orchestrator import (
    DEFAULT_EXTRA_TOOLS_MODULES,
    EXTRA_TOOLS_MODULES_ALL,
    R2_BUDGET_CAP,
    BenchmarkError,
    Orchestrator,
)
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils import llm_helper

REPO_ROOT = Path(__file__).resolve().parents[3]

#: 与 r2 测试同款的假 key（只为构造注入面，绝不触网）。
FAKE_KEY = "sk-test-abcdefabcdefabcdef"


# ---------------------------------------------------------------------------------------------
# config 单例键的直改夹具（test_engine_guards.py 模式）
# ---------------------------------------------------------------------------------------------

_CONFIG_KEYS_USED = ("PLANNER_ASSERT_DISCIPLINE", "SANDBOX_DISABLED", "PROJECT_SOURCE_ROOT")


@pytest.fixture()
def config_key() -> Iterator[Any]:
    """Set/restore a key in the global config dict (the singleton only merges env at construction)."""

    def _set(key: str, value: str | None) -> None:
        config = get_global_conf().get_config()
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value

    yield _set
    config = get_global_conf().get_config()
    for key in _CONFIG_KEYS_USED:
        config.pop(key, None)


# ---------------------------------------------------------------------------------------------
# T1 — R3-1 nav 补全上限
# ---------------------------------------------------------------------------------------------

NavCapEnv = "NAV_MAX_COMPLETION_TOKENS"


class _CaptureChatOpenAI:
    """构造 kwargs 捕获桩：绝不触网。"""

    instances: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _CaptureChatOpenAI.instances.append(kwargs)


@pytest.fixture()
def captured_chat_model(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    _CaptureChatOpenAI.instances = []
    monkeypatch.setattr(llm_helper, "ChatOpenAI", _CaptureChatOpenAI)
    return _CaptureChatOpenAI.instances


def _nav_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(NavCapEnv, raising=False)
    else:
        monkeypatch.setenv(NavCapEnv, value)


def test_t1_nav_cap_overrides_adapt_default(monkeypatch: pytest.MonkeyPatch, captured_chat_model: list[dict[str, Any]]) -> None:
    """env 未设/"0" → 4096（r2 真实基线，model_utils 兜底）；env=768 → 无条件覆盖为 768。"""
    _nav_env(monkeypatch, None)
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"temperature": 0.0})
    assert captured_chat_model[-1]["max_tokens"] == 4096

    _nav_env(monkeypatch, "0")
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"temperature": 0.0})
    assert captured_chat_model[-1]["max_tokens"] == 4096

    _nav_env(monkeypatch, "768")
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"temperature": 0.0})
    assert captured_chat_model[-1]["max_tokens"] == 768


def test_t1_nav_cap_beats_explicit_max_tokens(monkeypatch: pytest.MonkeyPatch, captured_chat_model: list[dict[str, Any]]) -> None:
    """显式 max_tokens=256 + env=768 → 768（env 最高优先级）；env 未设 → 显式值原样（现状不变）。"""
    _nav_env(monkeypatch, "768")
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"max_tokens": 256})
    assert captured_chat_model[-1]["max_tokens"] == 768

    _nav_env(monkeypatch, None)
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"max_tokens": 256})
    assert captured_chat_model[-1]["max_tokens"] == 256


def test_t1_planner_path_never_carries_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """planner 走裸 ChatOpenAI：env=768 下产物仍 max_tokens == 4096（benchmark 路径的 adapt 注入值）。"""
    from testzeus_hercules.core.agents import high_level_planner_agent as hlpa
    from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model

    captured: list[dict[str, Any]] = []

    class _PlannerStub:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(hlpa, "ChatOpenAI", _PlannerStub)
    monkeypatch.setattr(hlpa, "get_user_ltm", lambda: None)
    _nav_env(monkeypatch, "768")
    monkeypatch.delenv("LLM_PLANNER_REQUEST_TIMEOUT", raising=False)

    # 与 benchmark 一致的入参：SimpleHercules.create 先对 llm_config_params 做 adapt（注入 4096）
    llm_params = adapt_llm_params_for_model("glm-5.3", {"temperature": 0.0})
    assert llm_params["max_tokens"] == 4096
    hlpa.PlannerAgent({"model_name": "glm-5.3"}, llm_params)
    assert captured[-1]["max_tokens"] == 4096
    assert captured[-1]["max_tokens"] != 768


# ---------------------------------------------------------------------------------------------
# T2 — R3-2 planner 超时分档（双层）
# ---------------------------------------------------------------------------------------------

_PLANNER_ENV = "LLM_PLANNER_REQUEST_TIMEOUT"


class _SlowLLM:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def ainvoke(self, messages: list[Any]) -> str:
        await asyncio.sleep(self.delay)
        return "ok"


def _run_ainvoke(agent_name: str, delay: float) -> Any:
    from testzeus_hercules.core.simple_hercules import SimpleHercules

    instance = SimpleHercules.__new__(SimpleHercules)  # 绕过 init：_llm_ainvoke 不读 self 状态
    return asyncio.run(SimpleHercules._llm_ainvoke(instance, _SlowLLM(delay), [], agent_name))


def test_t2_wait_for_layer_planner_vs_nav(monkeypatch: pytest.MonkeyPatch) -> None:
    """planner 吃专属超时（0.5s），nav 吃 LLM_REQUEST_TIMEOUT（0.1s）——同一 stub llm 两档结果。"""
    monkeypatch.setenv(_PLANNER_ENV, "0.5")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "0.1")
    assert _run_ainvoke("planner_agent", 0.3) == "ok"  # 0.3s < 0.5s 专属窗口
    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        _run_ainvoke("browser_nav_agent", 0.3)  # 0.3s > 0.1s 共享窗口


def test_t2_wait_for_layer_unset_and_invalid_follow_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设/非法值 → planner 跟随 LLM_REQUEST_TIMEOUT（r2 parity），不抛 helper 层异常。"""
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "0.1")
    monkeypatch.delenv(_PLANNER_ENV, raising=False)
    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        _run_ainvoke("planner_agent", 0.3)

    monkeypatch.setenv(_PLANNER_ENV, "not-a-number")
    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        _run_ainvoke("planner_agent", 0.3)

    monkeypatch.setenv(_PLANNER_ENV, "0")  # <=0 同样回退
    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        _run_ainvoke("planner_agent", 0.3)


def _planner_timeout_kwargs(monkeypatch: pytest.MonkeyPatch, planner_env: str | None) -> dict[str, Any]:
    """构造 PlannerAgent（stub ChatOpenAI + stub LTM）并返回捕获的构造 kwargs。"""
    from testzeus_hercules.core.agents import high_level_planner_agent as hlpa
    from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model

    captured: list[dict[str, Any]] = []

    class _Stub:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(hlpa, "ChatOpenAI", _Stub)
    monkeypatch.setattr(hlpa, "get_user_ltm", lambda: None)
    if planner_env is None:
        monkeypatch.delenv(_PLANNER_ENV, raising=False)
    else:
        monkeypatch.setenv(_PLANNER_ENV, planner_env)
    llm_params = adapt_llm_params_for_model("glm-5.3", {"temperature": 0.0})
    hlpa.PlannerAgent({"model_name": "glm-5.3"}, llm_params)
    return captured[-1]


def test_t2_provider_layer_same_source_for_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 层同源：env=150 → planner 产物 timeout==150 且 max_tokens==4096（不受 R3-1 影响）。"""
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "90")
    kwargs = _planner_timeout_kwargs(monkeypatch, "150")
    assert kwargs["timeout"] == 150
    assert kwargs["max_tokens"] == 4096

    # nav（create_chat_model）不受 planner env 影响：仍 90
    nav_captured: list[dict[str, Any]] = []

    class _NavStub:
        def __init__(self, **kwargs: Any) -> None:
            nav_captured.append(kwargs)

    monkeypatch.setattr(llm_helper, "ChatOpenAI", _NavStub)
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"temperature": 0.0})
    assert nav_captured[-1]["timeout"] == 90
    assert nav_captured[-1]["max_tokens"] == 4096


def test_t2_provider_layer_unset_is_r2_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 未设 → planner 与 nav 产物 timeout 同为 90（r2 逐字节复现）。"""
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "90")
    kwargs = _planner_timeout_kwargs(monkeypatch, None)
    assert kwargs["timeout"] == 90

    nav_captured: list[dict[str, Any]] = []

    class _NavStub:
        def __init__(self, **kwargs: Any) -> None:
            nav_captured.append(kwargs)

    monkeypatch.setattr(llm_helper, "ChatOpenAI", _NavStub)
    llm_helper.create_chat_model({"model": "glm-5.3-flash", "api_key": FAKE_KEY}, {"temperature": 0.0})
    assert nav_captured[-1]["timeout"] == 90


# ---------------------------------------------------------------------------------------------
# T3 — E1 extra_tools 子集加载（subprocess 隔离）
# ---------------------------------------------------------------------------------------------

_T3_SCRIPT = "import testzeus_hercules.core.extra_tools as et; " "names = dir(et); " "print(int('drag_and_drop' in names), int('persist_findings' in names), int('read_clipboard' in names))"


def _t3_run(extra_env: dict[str, str]) -> tuple[bool, bool, bool]:
    env = dict(os.environ)
    env.update({"IS_TEST_ENV": "true", "ENABLE_TELEMETRY": "0"})
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _T3_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    drag, persist, clipboard = proc.stdout.strip().splitlines()[-1].split()
    return drag == "1", persist == "1", clipboard == "1"


def test_t3_subset_loads_only_the_allowlisted_module() -> None:
    """子集：drag 在、persist_findings/read_clipboard 不在（W2 类结构性消失）。"""
    drag, persist, clipboard = _t3_run({"LOAD_EXTRA_TOOLS": "true", "EXTRA_TOOLS_MODULES": "drag_and_drop_tool"})
    assert drag is True and persist is False and clipboard is False


def test_t3_no_env_and_empty_env_are_full_load() -> None:
    """无 env / 空串 → 全量（r2 复现通道）。"""
    for modules_env in ({}, {"EXTRA_TOOLS_MODULES": ""}):
        drag, persist, clipboard = _t3_run({"LOAD_EXTRA_TOOLS": "true", **modules_env})
        assert drag is True and persist is True and clipboard is True, modules_env


# ---------------------------------------------------------------------------------------------
# T4 — E2 文件工具调用日志行
# ---------------------------------------------------------------------------------------------


def test_t4_file_tool_log_lines(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """三个文件工具入口各记一行 [EXTRA_TOOL_CALL] <name> path=…（不打印内容）。"""
    from testzeus_hercules.core.extra_tools.file_handler_tool import (
        augment_findings,
        persist_findings,
        recall_findings,
    )

    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    path = tmp_path / "findings.txt"

    assert "Successfully wrote" in persist_findings(str(path), "hello")
    assert "[EXTRA_TOOL_CALL] persist_findings" in caplog.text and str(path) in caplog.text
    caplog.clear()

    assert recall_findings(str(path)) == "hello"
    assert "[EXTRA_TOOL_CALL] recall_findings" in caplog.text and str(path) in caplog.text
    caplog.clear()

    assert "Successfully appended" in augment_findings(str(path), " world")
    assert "[EXTRA_TOOL_CALL] augment_findings" in caplog.text and str(path) in caplog.text
    assert "hello world" not in caplog.text  # 内容不落日志


# ---------------------------------------------------------------------------------------------
# T5 — E3 open_url scheme 白名单
# ---------------------------------------------------------------------------------------------


class _StubPage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []
        self.evaluate_calls: list[str] = []
        self.url = "about:blank"

    async def goto(self, url: str, timeout: int | None = None) -> Any:
        self.goto_calls.append(url)
        self.url = url
        return SimpleNamespace(status=200, ok=True)

    async def evaluate(self, script: str) -> None:
        self.evaluate_calls.append(script)

    async def wait_for_load_state(self, state: str | None = None) -> None:
        return None

    async def title(self) -> str:
        return "Stubbed Title"


class _StubManager:
    def __init__(self, page: _StubPage) -> None:
        self.page = page
        self.screenshots: list[str] = []

    async def get_browser_context(self) -> None:
        return None

    async def reuse_or_create_tab(self, force_new_tab: bool = False) -> _StubPage:
        return self.page

    async def take_screenshots(self, name: str, page: Any) -> None:
        self.screenshots.append(name)

    async def wait_for_load_state_if_enabled(self, page: Any, state: str) -> None:
        return None

    async def wait_for_page_and_frames_load(self) -> None:
        return None


class _StubBrowserLogger:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def log_browser_interaction(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.fixture()
def stubbed_browser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_key: Any) -> Iterator[tuple[_StubPage, _StubBrowserLogger]]:
    """顶掉 open_url 的浏览器/日志面（review-r3 S3②：成功路径还须 stub take_screenshots + browser_logger）。"""
    # 包命名空间里的 ``open_url`` 属性已被 star-import 的同名函数遮蔽，须按模块对象取。
    open_url_module = importlib.import_module("testzeus_hercules.core.tools.open_url")

    page = _StubPage()
    manager = _StubManager(page)
    browser_logger = _StubBrowserLogger()
    monkeypatch.setattr(open_url_module, "PlaywrightManager", lambda: manager)
    monkeypatch.setattr(open_url_module, "get_browser_logger", lambda proof_path: browser_logger)
    config_key("PROJECT_SOURCE_ROOT", str(tmp_path))  # proof 目录落到 tmp
    yield page, browser_logger


def test_t5_dangerous_schemes_blocked_without_navigation(stubbed_browser: tuple[_StubPage, _StubBrowserLogger], caplog: pytest.LogCaptureFixture) -> None:
    """(a)(b)(e)：javascript:/data:/file: 一律拒绝、零导航调用、日志含 [OPEN_URL_BLOCKED]。"""
    open_url_module = importlib.import_module("testzeus_hercules.core.tools.open_url")

    caplog.set_level(logging.WARNING, logger="testzeus_hercules.utils.logger")
    page, _ = stubbed_browser

    result = asyncio.run(open_url_module.open_url("javascript:void(0)"))
    assert "Blocked URL scheme 'javascript:'" in result
    assert page.goto_calls == [] and page.evaluate_calls == []
    assert "[OPEN_URL_BLOCKED]" in caplog.text

    caplog.clear()
    result = asyncio.run(open_url_module.open_url("data:text/html,x"))
    assert "Blocked URL scheme 'data:'" in result
    result = asyncio.run(open_url_module.open_url("file:///etc/passwd"))
    assert "Blocked URL scheme 'file:'" in result
    assert page.goto_calls == [] and page.evaluate_calls == []  # 全程零导航


def test_t5_scheme_less_url_and_about_blank_keep_behaviour(stubbed_browser: tuple[_StubPage, _StubBrowserLogger]) -> None:
    """(c)(d)：无 scheme 输入走 ensure_protocol 正常导航；about:blank 走既有 special 分支。"""
    open_url_module = importlib.import_module("testzeus_hercules.core.tools.open_url")

    page, browser_logger = stubbed_browser

    result = asyncio.run(open_url_module.open_url("127.0.0.1:8462/x", timeout=0))
    assert "Page loaded" in result
    assert page.goto_calls == ["https://127.0.0.1:8462/x"]
    assert browser_logger.calls and browser_logger.calls[-1]["success"] is True

    page.evaluate_calls.clear()
    result = asyncio.run(open_url_module.open_url("about:blank"))
    assert "Navigated to about:blank" in result
    assert any("about:blank" in script for script in page.evaluate_calls)  # special 分支回归


# ---------------------------------------------------------------------------------------------
# T6 — E5 沙箱关停
# ---------------------------------------------------------------------------------------------


def test_t6_sandbox_disabled_refuses_before_any_marker(config_key: Any, caplog: pytest.LogCaptureFixture) -> None:
    """SANDBOX_DISABLED=true → 返回文本含 [SANDBOX_DISABLED]，不出现任何 r2 调用日志行。"""
    sandbox_module = importlib.import_module("testzeus_hercules.core.tools.execute_python_sandbox")

    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    config_key("SANDBOX_DISABLED", "true")
    result = asyncio.run(sandbox_module.execute_python_sandbox("whatever.py"))
    assert "[SANDBOX_DISABLED]" in result
    assert "Using sandbox tenant" not in caplog.text
    assert "Executing Python sandbox:" not in caplog.text


def test_t6_sandbox_enabled_walks_the_original_path(config_key: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """off（默认）→ 走原路径：首行日志 = r2 现状行；stub 页面缺失即走现有 no-page 错误返回。"""
    sandbox_module = importlib.import_module("testzeus_hercules.core.tools.execute_python_sandbox")

    class _NoPageManager:
        async def get_current_page(self) -> None:
            return None

    monkeypatch.setattr(sandbox_module, "PlaywrightManager", _NoPageManager)
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    config_key("SANDBOX_DISABLED", "false")

    result = asyncio.run(sandbox_module.execute_python_sandbox("whatever.py"))
    assert json.loads(result)["success"] is False  # 现有 no-page 错误 JSON
    info_lines = [record for record in caplog.records if "SANDBOX_DISABLED" not in record.getMessage()]
    assert "Executing Python sandbox: file=whatever.py" in info_lines[0].getMessage()  # r2 首行不变


# ---------------------------------------------------------------------------------------------
# T7 — 扫描标记
# ---------------------------------------------------------------------------------------------


def _scan_log_text(extra_lines: tuple[str, ...]) -> str:
    lines = [
        "# run_id: miniwob__x",
        "[2026-09-23 10:00:00] INFO {open_url.py:29} - Opening URL: http://127.0.0.1:8462/miniwob/click-button.html?r2g_seed=7&r2g_ms=240000 (force_new_tab=False)",
        *extra_lines,
    ]
    return "\n".join(lines) + "\n"


def test_t7_file_tool_open_url_and_sandbox_markers(tmp_path: Path) -> None:
    """E2 命中 → file_tool_invoked；E3 命中 → 仅 flagged；E5 标记 → sandbox_tool_invoked；干净 → 中性。"""
    log = tmp_path / "stdout.log"
    subdomain, seed = "click-button", 7

    log.write_text(_scan_log_text(()), encoding="utf-8")
    assert orchestrator.scan_cell_log(log, subdomain=subdomain, seed=seed) == orchestrator.CellScan(task_url_navigations=1)  # 基线日志本就含 1 次导航，标记全中性

    log.write_text(_scan_log_text(("[2026-09-23 10:00:05] INFO {file_handler_tool.py:31} - [EXTRA_TOOL_CALL] persist_findings path=/tmp/f.json",)), encoding="utf-8")
    scan = orchestrator.scan_cell_log(log, subdomain=subdomain, seed=seed)
    assert scan.flagged is True and scan.invalid_reason == orchestrator.INVALID_REASON_FILE_TOOL

    log.write_text(_scan_log_text(("[2026-09-23 10:00:06] WARNING {open_url.py:100} - [OPEN_URL_BLOCKED] scheme=javascript url=javascript:void(0)",)), encoding="utf-8")
    scan = orchestrator.scan_cell_log(log, subdomain=subdomain, seed=seed)
    assert scan.flagged is True and scan.invalid_reason is None  # 披露，不判无效

    log.write_text(
        _scan_log_text(("[2026-09-23 10:00:07] WARNING {execute_python_sandbox.py:66} - [SANDBOX_DISABLED] execute_python_sandbox refused (disabled for this environment)",)), encoding="utf-8"
    )
    scan = orchestrator.scan_cell_log(log, subdomain=subdomain, seed=seed)
    assert scan.flagged is True and scan.invalid_reason == orchestrator.INVALID_REASON_SANDBOX

    # 混合：文件工具 + 沙箱标记同时命中 → 两个理由按扫描序拼接；open_url-blocked 只加 flagged
    log.write_text(
        _scan_log_text(
            (
                "[EXTRA_TOOL_CALL] recall_findings path=/tmp/f.json",
                "[SANDBOX_DISABLED] refused",
                "[OPEN_URL_BLOCKED] scheme=data url=data:text/html,x",
            )
        ),
        encoding="utf-8",
    )
    scan = orchestrator.scan_cell_log(log, subdomain=subdomain, seed=seed)
    assert scan.invalid_reason == f"{orchestrator.INVALID_REASON_SANDBOX}; {orchestrator.INVALID_REASON_FILE_TOOL}"
    assert scan.flagged is True


# ---------------------------------------------------------------------------------------------
# T8 — E4 metrics clean 口径
# ---------------------------------------------------------------------------------------------

_T8_TASKS = [{"task_id": "t1"}, {"task_id": "t2"}, {"task_id": "t3"}]


def _t8_rows() -> list[dict[str, Any]]:
    return [
        {"task_id": "t1", "seed": 1, "status": "official_passed", "official_passed": True, "invalid_reason": "file_tool_invoked"},
        {"task_id": "t2", "seed": 1, "status": "official_failed", "official_passed": False, "invalid_reason": "sandbox_tool_invoked"},
        {"task_id": "t3", "seed": 1, "status": "official_passed", "official_passed": True, "invalid_reason": None},
    ]


def test_t8_clean_rate_removes_invalid_cells_from_both() -> None:
    """overall 分母/口径零改动；clean 把两个 invalid 格同时移出分子与分母；invalid_cells 恰两格带 reason。

    实现说明：spec T8 的"overall = 1/3"按其行构造（invalid&passed=True + invalid&failed + 正常通过）
    在 `_rate` 零改动约束下应为 2/3 —— `_rate` 把 official_passed=True 一律计 1（r2 官方口径，含
    invalid-but-passed 格），spec §5.4/§10 明令禁止改动该语义，故本断言锁定 2/3。
    """
    rows = _t8_rows()
    summary = summarize(rows, tasks=_T8_TASKS, exp_id="t8")
    assert summary.overall.passed == 2 and summary.overall.total == 3  # 官方口径不变
    assert summary.overall.value == pytest.approx(2 / 3)
    assert summary.clean is not None
    assert summary.clean.passed == 1 and summary.clean.total == 1
    assert summary.clean.value == pytest.approx(1.0)
    assert summary.clean.missing == ()
    assert summary.invalid_cells == [
        {"task_id": "t1", "seed": 1, "invalid_reason": "file_tool_invoked"},
        {"task_id": "t2", "seed": 1, "invalid_reason": "sandbox_tool_invoked"},
    ]
    assert summary.invalid_cells_total == 2
    assert summary.as_dict()["clean"] == summary.clean.as_dict()


def test_t8_clean_rate_missing_cells_still_count_zero() -> None:
    """无行 cell 在 clean 里语义不变：计 0 并列入 missing（spec §5.4）。"""
    rows = [{"task_id": "t1", "seed": 1, "status": "official_failed", "official_passed": False, "invalid_reason": "sandbox_tool_invoked"}]
    value = clean_rate(latest_rows(rows)[0], _T8_TASKS)
    assert value.passed == 0 and value.total == 2  # t1 移出；t2/t3 仍计 0
    assert value.missing == ("t2", "t3")


def test_t8_invalid_cells_truncated_at_50_with_total() -> None:
    """>50 个 invalid → 列表截 50 条、total 记全量（截断可审计）。"""
    rows = [{"task_id": f"t{index:03d}", "seed": 1, "status": "official_failed", "official_passed": False, "invalid_reason": "file_tool_invoked"} for index in range(60)]
    tasks = [{"task_id": f"t{index:03d}"} for index in range(60)]
    cells, _ = latest_rows(rows)
    entries, total = invalid_cell_list(cells)
    assert len(entries) == 50 and total == 60
    assert [entry["task_id"] for entry in entries] == sorted(entry["task_id"] for entry in entries)
    summary = summarize(rows, tasks=tasks)
    assert summary.invalid_cells_total == 60 and len(summary.invalid_cells) == 50


# ---------------------------------------------------------------------------------------------
# T9 — R3-3 drag 选择器解析
# ---------------------------------------------------------------------------------------------


class _StubElement:
    def __init__(self, name: str) -> None:
        self.name = name
        self.box = {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0}

    async def scroll_into_view_if_needed(self) -> None:
        return None

    async def bounding_box(self) -> dict[str, float]:
        return self.box


class _DragPage:
    def __init__(self) -> None:
        self.wait_calls: list[str] = []
        self.mouse_moves = 0
        self.mouse_downs = 0
        self.mouse_ups = 0
        self.mouse = _DragMouse(self)

    async def wait_for_selector(self, selector: str, timeout: int = 2000) -> Any:
        self.wait_calls.append(selector)
        return _StubElement(selector)

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - not used by the tool
        return None


class _DragMouse:
    def __init__(self, page: _DragPage) -> None:
        self.page = page

    async def move(self, x: float, y: float) -> None:
        self.page.mouse_moves += 1

    async def down(self) -> None:
        self.page.mouse_downs += 1

    async def up(self) -> None:
        self.page.mouse_ups += 1


@pytest.fixture()
def drag_harness(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], _DragPage, dict[str, Any]]:
    """stub find_element（按候选表返回）+ stub page/mouse；把 asyncio.sleep 压成零延时。"""
    from testzeus_hercules.core.extra_tools import drag_and_drop_tool as drag_module

    find_calls: list[str] = []
    results: dict[str, Any] = {}

    async def fake_find_element(selector: str, page: Any, element_name: str | None = None) -> Any:
        find_calls.append(selector)
        return results.get(selector)

    page = _DragPage()

    class _Manager:
        async def get_current_page(self) -> Any:
            return page

        find_element = staticmethod(fake_find_element)

    monkeypatch.setattr(drag_module, "PlaywrightManager", _Manager)
    monkeypatch.setattr(drag_module, "get_global_conf", lambda: SimpleNamespace(get_delay_time=lambda: 0.0))

    async def _no_sleep(_seconds: float | None = None) -> None:
        return None

    monkeypatch.setattr(drag_module.asyncio, "sleep", _no_sleep)
    return find_calls, page, results


def _run_drag(source: str, target: str = "css=.drop-zone") -> str:
    from testzeus_hercules.core.extra_tools.drag_and_drop_tool import drag_and_drop

    return asyncio.run(drag_and_drop(source, target))


def test_t9_explicit_selector_passes_through(drag_harness: tuple[list[str], _DragPage, dict[str, Any]]) -> None:
    """(a)：css= 前缀逐字透传（不包 md），鼠标 down→20 move→up 序列执行，返回成功文案。"""
    find_calls, page, results = drag_harness
    results["css=.drag-handle"] = _StubElement("source")
    result = _run_drag("css=.drag-handle")
    assert find_calls == ["css=.drag-handle"]
    assert page.mouse_downs == 1 and page.mouse_ups == 1
    assert page.mouse_moves >= 21  # 1 起手 + 20 步进 + 1 收尾
    assert "Successfully performed drag and drop" in result


def test_t9_bare_md_id_keeps_r2_fallback(drag_harness: tuple[list[str], _DragPage, dict[str, Any]]) -> None:
    """(b)(e)：裸数字 → 首候选 [md='5']；显式 md=7 → [md='7']；target 侧行为零变化。"""
    find_calls, page, results = drag_harness
    results["[md='5']"] = _StubElement("source5")
    result = _run_drag("5")
    assert find_calls == ["[md='5']"]
    assert "Successfully" in result

    results["[md='7']"] = _StubElement("source7")
    find_calls.clear()
    page.wait_calls.clear()
    result = _run_drag("md=7", "css=.drop-zone")
    assert find_calls == ["[md='7']"]
    assert page.wait_calls == ["css=.drop-zone"]  # target 原样进 wait_for_selector
    assert "Successfully" in result


def test_t9_unknown_shape_falls_back_in_order(drag_harness: tuple[list[str], _DragPage, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """(c)：未知形态依次尝试 原样 → [md='…'] → text='…'，前两个 None、第三个命中。"""
    from testzeus_hercules.core.extra_tools import drag_and_drop_tool as drag_module

    find_calls, _, _ = drag_harness

    async def fake_find_element(selector: str, page: Any, element_name: str | None = None) -> Any:
        find_calls.append(selector)
        return _StubElement(selector) if selector == "text='Kass'" else None

    class _Manager:
        async def get_current_page(self) -> Any:
            return _DragPage()

        find_element = staticmethod(fake_find_element)

    monkeypatch.setattr(drag_module, "PlaywrightManager", _Manager)
    result = _run_drag("Kass")
    assert find_calls == ["Kass", "[md='Kass']", "text='Kass'"]
    assert "Successfully" in result


def test_t9_all_candidates_fail_lists_them(drag_harness: tuple[list[str], _DragPage, dict[str, Any]]) -> None:
    """(d)：全候选失败 → 错误消息列出全部候选。"""
    find_calls, _, _ = drag_harness
    result = _run_drag("Kass")
    assert find_calls == ["Kass", "[md='Kass']", "text='Kass'"]
    assert "Source element not found using any of these selectors" in result
    for candidate in ("Kass", "[md='Kass']", "text='Kass'"):
        assert candidate in result


# ---------------------------------------------------------------------------------------------
# T10 — orchestrator 注入与 flags
# ---------------------------------------------------------------------------------------------

RoutingEnvKeys = frozenset({"AGENTS_LLM_CONFIG_FILE", "AGENTS_LLM_CONFIG_FILE_REF_KEY", "MODEL_API_KEY", "OPENAI_API_KEY"})


def _full_on_view(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Orchestrator:
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)
    view = Orchestrator(
        "miniwob-r3",
        stage="pilot",
        exp_root=tmp_path / "dev_runs",
        tasks=tasks,
        terminal_cue=True,
        single_start=True,
        role_routing=True,
        latency_env=True,
        extra_tools=True,
        template_notes=True,
        nav_max_tokens=768,
        planner_timeout=150,
        disable_sandbox=True,
        assert_discipline=True,
        **overrides,
    )
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    view._routing_config_path = view._prepare_role_routing()
    return view


def test_t10_full_on_env_key_set_is_exact(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(a)：全开 → 恰为 r2 五键 + LOAD_EXTRA_TOOLS + 路由 4 键 + 新五 env，多一个键即失败。"""
    view = _full_on_view(tasks, tmp_path, monkeypatch)
    env = view._child_extra_env()
    expected = (
        set(orchestrator.LATENCY_ENV_OVERRIDES)
        | {"LOAD_EXTRA_TOOLS"}
        | set(RoutingEnvKeys)
        | {
            "EXTRA_TOOLS_MODULES",
            "NAV_MAX_COMPLETION_TOKENS",
            "LLM_PLANNER_REQUEST_TIMEOUT",
            "SANDBOX_DISABLED",
            "PLANNER_ASSERT_DISCIPLINE",
        }
    )
    assert set(env) == expected
    assert len(env) == 15
    assert env["EXTRA_TOOLS_MODULES"] == "drag_and_drop_tool"
    assert env["NAV_MAX_COMPLETION_TOKENS"] == "768"
    assert env["LLM_PLANNER_REQUEST_TIMEOUT"] == "150"
    assert env["SANDBOX_DISABLED"] == "true"
    assert env["PLANNER_ASSERT_DISCIPLINE"] == "true"


def test_t10_default_off_injects_no_new_env(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """(b)：r3 flags 全 off → 新五 env 零出现（latency 开着也不出现后两个）。"""
    view = Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, latency_env=True)
    env = view._child_extra_env()
    assert env == dict(orchestrator.LATENCY_ENV_OVERRIDES)
    for key in ("EXTRA_TOOLS_MODULES", "NAV_MAX_COMPLETION_TOKENS", "LLM_PLANNER_REQUEST_TIMEOUT", "SANDBOX_DISABLED", "PLANNER_ASSERT_DISCIPLINE"):
        assert key not in env


def test_t10_extra_tools_all_records_sentinel_and_no_env(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """(c)：modules=all → 无 EXTRA_TOOLS_MODULES 键，flags 记 ["__all__"]。"""
    view = Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, extra_tools=True, extra_tools_modules=" ALL ")
    env = view._child_extra_env()
    assert env == {"LOAD_EXTRA_TOOLS": "true"}
    assert view.flags["extra_tools_modules"] == [EXTRA_TOOLS_MODULES_ALL]


def test_t10_empty_modules_with_extra_tools_is_an_error(tasks: list[dict[str, Any]]) -> None:
    """(d)：--extra-tools + 空 modules → BenchmarkError（防误开全量）。"""
    with pytest.raises(BenchmarkError):
        Orchestrator("e", stage="pilot", tasks=tasks, extra_tools=True, extra_tools_modules="   ")


def test_t10_manifest_flags_carry_the_five_new_keys(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """(e)：manifest flags 含五个新键及取值；0 值不注入对应 env。"""
    view = Orchestrator(
        "e",
        stage="pilot",
        exp_root=tmp_path / "dev_runs",
        tasks=tasks,
        extra_tools=True,
        nav_max_tokens=768,
        planner_timeout=150,
        disable_sandbox=True,
        assert_discipline=True,
    )
    flags = view.flags
    assert flags["nav_max_tokens"] == 768
    assert flags["planner_timeout"] == 150
    assert flags["extra_tools_modules"] == ["drag_and_drop_tool"]
    assert flags["disable_sandbox"] is True and flags["assert_discipline"] is True

    env = view._child_extra_env()
    assert env["NAV_MAX_COMPLETION_TOKENS"] == "768" and env["LLM_PLANNER_REQUEST_TIMEOUT"] == "150"
    assert env["SANDBOX_DISABLED"] == "true" and env["PLANNER_ASSERT_DISCIPLINE"] == "true"
    assert env["EXTRA_TOOLS_MODULES"] == DEFAULT_EXTRA_TOOLS_MODULES

    zero_view = Orchestrator(
        "e2",
        stage="pilot",
        exp_root=tmp_path / "dev_runs2",
        tasks=tasks,
        latency_env=True,
        nav_max_tokens=0,
        planner_timeout=0,
    )
    zero_env = zero_view._child_extra_env()
    assert "NAV_MAX_COMPLETION_TOKENS" not in zero_env and "LLM_PLANNER_REQUEST_TIMEOUT" not in zero_env
    assert zero_view.flags["extra_tools_modules"] == []


# ---------------------------------------------------------------------------------------------
# T11 — R3-6 断言纪律开关
# ---------------------------------------------------------------------------------------------


def test_t11_assert_discipline_on_and_off(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """conf true → system_message 以纪律段开头且含 is_passed=false；false/未设 → 与 r2 逐字节一致。"""
    from string import Template

    from testzeus_hercules.core.agents import high_level_planner_agent as hlpa

    class _Stub:
        def __init__(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(hlpa, "ChatOpenAI", _Stub)
    monkeypatch.setattr(hlpa, "get_user_ltm", lambda: None)
    monkeypatch.delenv("LLM_PLANNER_REQUEST_TIMEOUT", raising=False)

    config_key("PLANNER_ASSERT_DISCIPLINE", "true")
    on = hlpa.PlannerAgent({"model_name": "glm-5.3"}, {"temperature": 0.0})
    assert on.system_message.startswith(hlpa._ASSERT_DISCIPLINE_INSTRUCTION)
    assert "is_passed=false" in on.system_message

    config_key("PLANNER_ASSERT_DISCIPLINE", "false")
    off = hlpa.PlannerAgent({"model_name": "glm-5.3"}, {"temperature": 0.0})
    expected_r2 = hlpa.PlannerAgent._json_instruction + Template(hlpa.PlannerAgent.prompt).safe_substitute(basic_test_information="No test data provided")
    assert off.system_message == expected_r2  # r2 快照锁定

    config_key("PLANNER_ASSERT_DISCIPLINE", None)
    unset = hlpa.PlannerAgent({"model_name": "glm-5.3"}, {"temperature": 0.0})
    assert unset.system_message == expected_r2


# ---------------------------------------------------------------------------------------------
# T12 — R3-4 D 臂 dry-run（零代码组合验证）
# ---------------------------------------------------------------------------------------------


def test_t12_d_arm_dry_run_480s(tmp_path: Path, tasks: list[dict[str, Any]], caplog: pytest.LogCaptureFixture) -> None:
    """--stage full --dry-run --episode-ms 480000 --timeout-s 900 → 125 格、budget 130/144、零执行零落盘。"""
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")
    exp_root = tmp_path / "dev_runs"
    code = orchestrator.main(
        [
            "--exp-id",
            "miniwob-r3-d480",
            "--stage",
            "full",
            "--dry-run",
            "--episode-ms",
            "480000",
            "--timeout-s",
            "900",
            "--exp-root",
            str(exp_root),
        ]
    )
    assert code == 0
    assert not exp_root.exists()  # 零执行零落盘
    text = caplog.text
    assert text.count("orchestrator: miniwob.") == 125
    assert "r2g_ms=480000" in text  # episode 参数进 URL
    assert '"total": 130' in text and '"cap": 144' in text
    assert R2_BUDGET_CAP == 144

    view = Orchestrator("miniwob-r3-d480", stage="full", tasks=tasks, dry_run=True, episode_ms=480000, timeout_s=900)
    assert view.flags["nav_max_tokens"] == 0  # D 臂 = headline flags + 计时参数，此处仅验证计时面
    assert view.episode_ms == 480000 and view.timeout_s == 900
    assert orchestrator.hercules_budget("full")["total"] == 130
