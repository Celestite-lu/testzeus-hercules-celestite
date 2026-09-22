"""T9 — engine guards behind the r2 latency flags (spec-r2 §5.1): C4a refresh modes + C4b step budget.

The guards are exercised through the real ``SimpleHercules`` functions with a stub ``self`` — no LLM,
no browser.  The default-off invariant matters most: ``BROWSER_STATE_REFRESH_MODE=always`` (default),
``NAV_STEP_TIME_BUDGET_S=0`` (default) and an unset/invalid config must reproduce the r1 behaviour.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.simple_hercules import SimpleHercules

STATE_CHANGING_RESULT = "clicked the button"  # a successful state-changing tool result (no error words)
ERROR_RESULT = "[TOOL ERROR] click: element vanished"


def _stub() -> Any:
    """A minimal ``self`` carrying the real class constants (the function is passed ``self`` explicitly)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        _STATE_REFRESH_MARKERS=SimpleHercules._STATE_REFRESH_MARKERS,
        _BROWSER_STATE_CHANGING_TOOLS=SimpleHercules._BROWSER_STATE_CHANGING_TOOLS,
    )


@pytest.fixture()
def refresh_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Set/cleanup ``BROWSER_STATE_REFRESH_MODE`` in the global config dict."""

    def _set(mode: str | None) -> None:
        config = get_global_conf().get_config()
        if mode is None:
            config.pop("BROWSER_STATE_REFRESH_MODE", None)
        else:
            config["BROWSER_STATE_REFRESH_MODE"] = mode

    yield _set
    config = get_global_conf().get_config()
    config.pop("BROWSER_STATE_REFRESH_MODE", None)


def _refresh(tool_name: str = "click", tool_result: str = STATE_CHANGING_RESULT) -> bool:
    return SimpleHercules._requires_state_refresh(_stub(), "browser_nav_agent", tool_name, tool_result)


def test_t9_markers_only_lets_successful_state_changes_finish_the_batch(refresh_mode: Any) -> None:
    """markers_only：成功 click 不打断批内（返回 False → 剩余 tool calls 全执行）；markers 命中仍打断。"""
    refresh_mode("markers_only")
    assert _refresh() is False  # 成功状态变更工具不再打断
    assert _refresh(tool_result=ERROR_RESULT) is False  # 错误本就不打断
    marker_result = "new elements have appeared after this action"
    assert _refresh(tool_result=marker_result) is True  # markers 命中 → 两个模式一致
    assert _refresh(tool_name="get_page_text") is False
    # 非 browser_nav_agent 恒 False（与模式无关）
    assert SimpleHercules._requires_state_refresh(_stub(), "executor_nav_agent", "click", marker_result) is False


def test_t9_always_default_and_invalid_values_keep_the_r1_behaviour(refresh_mode: Any) -> None:
    """always（默认）/未配置/非法值/空串 → 现状逻辑：成功状态变更打断、错误不打断；大小写归一合法。"""
    for mode in ("always", None, "weird", "  "):
        refresh_mode(mode)
        assert _refresh() is True, mode  # 成功状态变更工具 → 打断（r1 行为）
        assert _refresh(tool_result=ERROR_RESULT) is False, mode  # 错误不打断（r1 行为）
        assert _refresh(tool_name="get_page_text") is False, mode  # 非状态变更工具不打断
    refresh_mode(" MARKERS_ONLY ")  # strip+lower 后合法 → markers_only
    assert _refresh() is False


# ---------------------------------------------------------------------------------------------
# C4b 步级预算
# ---------------------------------------------------------------------------------------------


class _Resp:
    def __init__(self, content: str = "", tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _StubRunner:
    """Drives the real ``_run_nav_agent`` without an LLM or a browser."""

    _STATE_REFRESH_MARKERS = SimpleHercules._STATE_REFRESH_MARKERS
    _BROWSER_STATE_CHANGING_TOOLS = SimpleHercules._BROWSER_STATE_CHANGING_TOOLS
    # shared real helpers — assigned on the class so normal method binding applies
    _tool_call_name = staticmethod(SimpleHercules._tool_call_name)
    _tool_call_args = staticmethod(SimpleHercules._tool_call_args)
    _tool_call_id = staticmethod(SimpleHercules._tool_call_id)
    _tool_call_for_history = SimpleHercules._tool_call_for_history
    _ai_message_with_tool_calls = SimpleHercules._ai_message_with_tool_calls
    _last_assistant_content = staticmethod(SimpleHercules._last_assistant_content)

    def __init__(self, responses: list[_Resp]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.nav_agent_number_of_rounds = 5

    async def _ensure_nav_agent_ready(self, nav_agent: Any) -> None:
        return None

    async def _llm_ainvoke(self, llm: Any, messages: list[Any], agent_name: str) -> Any:
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]

    def _record_nav_token_usage(self, agent_name: str, resp: Any) -> None:
        return None

    def _is_context_limit_error(self, exc: Any) -> bool:
        return False

    def _compress_messages(self, messages: list[Any]) -> list[Any]:
        return messages

    async def _execute_tool_call(self, tool_obj: Any, tool_name: str, tool_args: dict[str, Any]) -> str:
        return "clicked ok"

    def _requires_state_refresh(self, agent_name: str, tool_name: str, tool_result: str) -> bool:
        return False


class _Tool:
    name = "click"


class _LLM:
    def bind_tools(self, tools: list[Any]) -> "_LLM":
        return self


def _nav_agent() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(tools=[_Tool()], llm=_LLM(), system_message="system prompt")


@pytest.fixture()
def step_budget(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Set/cleanup ``NAV_STEP_TIME_BUDGET_S`` in the global config dict."""

    def _set(value: str | None) -> None:
        config = get_global_conf().get_config()
        if value is None:
            config.pop("NAV_STEP_TIME_BUDGET_S", None)
        else:
            config["NAV_STEP_TIME_BUDGET_S"] = value

    yield _set
    get_global_conf().get_config().pop("NAV_STEP_TIME_BUDGET_S", None)


def _patch_clock(monkeypatch: pytest.MonkeyPatch, readings: list[float]) -> None:
    """Replace ``time.monotonic`` for the engine module with a scripted clock (reverted by monkeypatch)."""
    import testzeus_hercules.core.simple_hercules as sh_module

    stream = iter(readings)
    last = {"t": readings[-1] if readings else 0.0}

    def fake_monotonic() -> float:
        try:
            value = next(stream)
        except StopIteration:
            value = last["t"]
        last["t"] = value
        return value

    monkeypatch.setattr(sh_module.time, "monotonic", fake_monotonic)


def test_t9_step_budget_exhaustion_returns_recognisable_marker(step_budget: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """超限：带当前进展返回 planner，文本含 ``[NAV_STEP_BUDGET_EXHAUSTED]``（可辨识、非异常）。"""
    step_budget("5")
    # 时钟读数：step_started=0 → turn1 顶部 1s（未超）→ turn1 执行 → turn2 顶部 10s（超限）
    _patch_clock(monkeypatch, [0.0, 1.0, 10.0])
    runner = _StubRunner(
        [
            _Resp(content="", tool_calls=[{"name": "click", "args": {"md": "123"}, "id": "t1"}]),
            _Resp(content="finished the click"),
        ]
    )
    result = asyncio.run(SimpleHercules._run_nav_agent(runner, _nav_agent(), "do the click", "browser_nav_agent"))
    assert "[NAV_STEP_BUDGET_EXHAUSTED] step budget 5s reached" in result
    assert result.startswith("[empty or tool-calls-only assistant response]")  # 带当前进展返回


def test_t9_step_budget_zero_unset_and_never_expiring_are_off(step_budget: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """预算 0（默认）/未配置：永不触发；轮数上限触发的现有返回文本保持。"""
    _patch_clock(monkeypatch, [100.0, 200.0, 300.0, 400.0])  # 读数巨大，预算 >0 才会误触发
    for value in ("0", None):
        step_budget(value)
        # 1) 正常终止路径不触发预算
        runner = _StubRunner([_Resp(content="##TERMINATE TASK##")])
        result = asyncio.run(SimpleHercules._run_nav_agent(runner, _nav_agent(), "task", "browser_nav_agent"))
        assert result == "##TERMINATE TASK##"
        assert "NAV_STEP_BUDGET" not in result
        # 2) 轮数耗尽仍走现有 max-rounds 文本（预算不打断它）
        runner = _StubRunner([_Resp(content="", tool_calls=[{"name": "click", "args": {}, "id": "t1"}])])
        result = asyncio.run(SimpleHercules._run_nav_agent(runner, _nav_agent(), "task", "browser_nav_agent"))
        assert result.startswith("[ERROR] browser_nav_agent max nav rounds (5)")
        assert "NAV_STEP_BUDGET" not in result
