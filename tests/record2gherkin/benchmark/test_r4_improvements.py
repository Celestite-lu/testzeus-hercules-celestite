"""r4 离线单测 T1–T10（spec-r4 §5）。

全部不跑真 LLM、零外网（T4 仅本机回环 + 本地 chromium，缺浏览器时 skip 不失败）；env 用
monkeypatch/config 直改隔离（与 test_r3_improvements.py 同法）。覆盖：

- T1  config 开关（MD_INTERACTIVE_EXTENDED / VERIFY_BEFORE_DONE：默认、getter、env 入 config）
- T2  orchestrator 注入与 flags（r4 全开键集恰增二、默认零注入、manifest 三新键、latency 回归）
- T3  flatten 过滤器（off golden 逐字节、extended 收录/role 白名单、150 上限、MF-1 class 存活）
- T4  真浏览器注入/终表验证（email-inbox 行/图标入表 + star/trash class 互异 + find-greatest 卡）
- T5  verify 路由（verify 触发/硬上限/is_passed=false 不核验/flag off = r3）
- T6  verify 全链（stub planner/executor：planner→verify→executor→planner→END、核验步内容）
- T7  扫描器与 metrics（offseed/零事件/never-started flagged-only、r3 形态回放 6 格）
- T8  补丁与服务端点（off golden 字节、beacon 追加、两端点 jsonl+204、奖励端点回归）
- T9  工具输出 parity（off 模式 JSON 与 r3 golden 一致；extended class 存活；空表文案）
- T10 回归由全量套件承担；本文件内 T2(d)/T7(d)/T8(d)/T3a/T9a 均带显式回归断言。

实现说明（对 spec 的两点偏离，均已最小化）：
1. T6 的"executor 恰执行 2 次"与 spec 自述执行序 planner→verify→executor→planner→END 不相容
   （该序只含 1 次 executor）。以执行序与语义为准：核验步经既有 executor 路径执行恰 1 次、
   ``_VERIFY_STEP`` 出现在该次执行的状态里、verify_rounds 全程 ≤1。
2. T4 的开局方式用伺服器 auto-start 补丁（conftest ``miniwob_server`` fixture + ``r2g_seed``
   URL + ``core.ept0`` 就绪等待），而非 spec 字面的 http.server + 手点 START 遮罩——两者到达
   的页面状态相同（vendored 页无补丁时才需要手点，review-r4 §3 实测记录），且少一个脆弱的
   遮罩选择器依赖。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest
from record2gherkin.benchmark import metrics as metrics_module
from record2gherkin.benchmark import orchestrator
from record2gherkin.benchmark.miniwob_server import (
    AUTO_START_PATCH,
    EPSTART_FILENAME,
    EPSTART_PATH,
    INTEGRITY_BEACON_PATCH,
    OFFSEED_FILENAME,
    OFFSEED_PATH,
    REWARD_HOOK_PATCH,
    MiniWobServer,
    patch_core_js,
)
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.simple_hercules import _VERIFY_STEP, SimpleHercules

REPO_ROOT = Path(__file__).resolve().parents[3]

#: 与 r2/r3 测试同款的假 key（只为构造注入面，绝不触网）。
FAKE_KEY = "sk-test-abcdefabcdefabcdef"

#: 零事件 6 格（analysis-r3-failures §4.5：NR 3 + 零事件 T 3），T7f 回放的命中集合。
ZERO_EVENT_SUBDOMAINS = ("text-editor", "email-inbox-delete", "social-media", "hot-cold", "find-greatest", "use-slider-2")


# ---------------------------------------------------------------------------------------------
# config 单例键的直改夹具（test_r3_improvements.py 模式）
# ---------------------------------------------------------------------------------------------

_CONFIG_KEYS_USED = ("MD_INTERACTIVE_EXTENDED", "VERIFY_BEFORE_DONE", "PROJECT_SOURCE_ROOT")


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
# T1 — config 开关
# ---------------------------------------------------------------------------------------------

_T1_SCRIPT = "from testzeus_hercules.config import get_global_conf; c = get_global_conf(); print(c.get_md_interactive_extended(), c.get_verify_before_done())"


def _t1_conf_getters(extra_env: dict[str, str]) -> tuple[str, str]:
    """Fresh-process config read: proves the env keys enter the config (relevant_keys) or not."""
    env = dict(os.environ)
    env.update({"IS_TEST_ENV": "true", "ENABLE_TELEMETRY": "0"})
    for key in ("MD_INTERACTIVE_EXTENDED", "VERIFY_BEFORE_DONE"):
        env.pop(key, None)
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _T1_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    extended, verify = proc.stdout.strip().splitlines()[-1].split()
    return extended, verify


def test_t1_defaults_and_relevant_keys_via_fresh_process() -> None:
    """(a) 默认 "false"/"false"（_finalize_defaults setdefault），且两键在 relevant_keys 中——
    fresh 进程里置 env 后 getter 跟随（env 不入 config 则恒 "false"，即测试失败）。"""
    assert _t1_conf_getters({}) == ("false", "false")
    assert _t1_conf_getters({"MD_INTERACTIVE_EXTENDED": "true", "VERIFY_BEFORE_DONE": "true"}) == ("true", "true")


def test_t1_getters_return_the_raw_value(config_key: Any) -> None:
    """(b) getter 原样返回配置值（strip/lower 由消费方做）；singleton 直改后 getter 跟随。"""
    conf = get_global_conf()
    assert conf.get_md_interactive_extended() == "false"
    assert conf.get_verify_before_done() == "false"

    config_key("MD_INTERACTIVE_EXTENDED", "true")
    config_key("VERIFY_BEFORE_DONE", " TRUE ")  # 原样透传，消费方 strip().lower()
    assert conf.get_md_interactive_extended() == "true"
    assert conf.get_verify_before_done() == " TRUE "

    config_key("MD_INTERACTIVE_EXTENDED", None)
    config_key("VERIFY_BEFORE_DONE", None)
    assert conf.get_md_interactive_extended() == "false"  # 键移除 → setdefault 语义的默认值
    assert conf.get_verify_before_done() == "false"


# ---------------------------------------------------------------------------------------------
# T2 — orchestrator 注入与 flags
# ---------------------------------------------------------------------------------------------

RoutingEnvKeys = frozenset({"AGENTS_LLM_CONFIG_FILE", "AGENTS_LLM_CONFIG_FILE_REF_KEY", "MODEL_API_KEY", "OPENAI_API_KEY"})


def _r4_headline_view(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> orchestrator.Orchestrator:
    """r4 headline 全开（r3 十项去 nav-max-tokens + 两新 flag；plan-r4 §3）。"""
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)
    view = orchestrator.Orchestrator(
        "miniwob-r4",
        stage="pilot",
        exp_root=tmp_path / "dev_runs",
        tasks=tasks,
        terminal_cue=True,
        single_start=True,
        role_routing=True,
        latency_env=True,
        extra_tools=True,
        template_notes=True,
        planner_timeout=150,
        disable_sandbox=True,
        assert_discipline=True,
        md_extended=True,
        verify_before_done=True,
        **overrides,
    )
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    view._routing_config_path = view._prepare_role_routing()
    return view


def test_t2a_r4_full_on_env_key_set_is_exact(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) r4 全开 → r3 headline 键集（去掉 NAV_MAX_COMPLETION_TOKENS）+ 两新键；多一个键即失败。"""
    view = _r4_headline_view(tasks, tmp_path, monkeypatch)
    env = view._child_extra_env()
    expected = (
        set(orchestrator.LATENCY_ENV_OVERRIDES)
        | {"LOAD_EXTRA_TOOLS"}
        | set(RoutingEnvKeys)
        | {
            "EXTRA_TOOLS_MODULES",
            "LLM_PLANNER_REQUEST_TIMEOUT",
            "SANDBOX_DISABLED",
            "PLANNER_ASSERT_DISCIPLINE",
            "MD_INTERACTIVE_EXTENDED",
            "VERIFY_BEFORE_DONE",
        }
    )
    assert set(env) == expected
    assert len(env) == 16
    assert "NAV_MAX_COMPLETION_TOKENS" not in env  # r4 headline 摘除（安全复审 §5 实证 no-op）
    assert env["MD_INTERACTIVE_EXTENDED"] == "true"
    assert env["VERIFY_BEFORE_DONE"] == "true"
    assert env["LLM_REQUEST_TIMEOUT"] == "90"  # latency 值抽查


def test_t2b_default_r4_off_injects_no_new_env(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """(b) 默认（无 r4 flags）→ 两新 env 零出现，flags 中两键为 false。"""
    view = orchestrator.Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks, latency_env=True)
    env = view._child_extra_env()
    assert env == dict(orchestrator.LATENCY_ENV_OVERRIDES)
    assert "MD_INTERACTIVE_EXTENDED" not in env and "VERIFY_BEFORE_DONE" not in env
    assert view.flags["md_interactive_extended"] is False and view.flags["verify_before_done"] is False


def test_t2c_manifest_flags_carry_the_three_new_keys(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(c)+(e) manifest flags 含三个新键（offseed_beacon 恒 true）且无 nav_max_tokens 键。"""
    view = _r4_headline_view(tasks, tmp_path, monkeypatch)
    assert view.flags["md_interactive_extended"] is True
    assert view.flags["verify_before_done"] is True
    assert view.flags["offseed_beacon"] is True
    assert "nav_max_tokens" not in view.flags  # spec-r4 §4：headline 缺省该键

    view._write_manifest()
    manifest = json.loads(view.manifest_path.read_text(encoding="utf-8"))
    assert manifest["flags"]["md_interactive_extended"] is True
    assert manifest["flags"]["verify_before_done"] is True
    assert manifest["flags"]["offseed_beacon"] is True
    assert "nav_max_tokens" not in manifest["flags"]


def test_t2d_latency_env_overrides_regression() -> None:
    """(d) LATENCY_ENV_OVERRIDES 五键值逐字节不动（回归）。"""
    assert orchestrator.LATENCY_ENV_OVERRIDES == {
        "LLM_REQUEST_TIMEOUT": "90",
        "LLM_MAX_RETRIES": "2",
        "BROWSER_STATE_REFRESH_MODE": "markers_only",
        "BROWSER_NAV_MAX_CHAT_ROUND": "30",
        "NAV_STEP_TIME_BUDGET_S": "120",
    }


def test_t2e_budget_manifest_carries_the_cumulative_account(tasks: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """预算全局护栏（plan-r4 §5）：manifest 逐调用执行数 + 跨调用累计账；Σ>144 量化披露。"""
    monkeypatch.setattr(orchestrator.runner_module, "read_api_key", lambda path=None: FAKE_KEY)
    view = orchestrator.Orchestrator("e", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    view.exp_dir.mkdir(parents=True, exist_ok=True)
    # 两个已执行行 + 一个 no_goal 行（不计入执行账）→ 累计 2
    executed = {"task_id": "x1", "seed": 1, "goal": "g"}
    no_goal = {"task_id": "x2", "seed": 1, "goal": None}
    view.results_path.write_text("".join(json.dumps(row) + "\n" for row in (executed, executed, no_goal)), encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="testzeus_hercules.utils.logger")
    view._write_manifest()
    manifest = json.loads(view.manifest_path.read_text(encoding="utf-8"))
    assert manifest["budget"]["cumulative_hercules_used"] == 2
    assert manifest["budget"]["cumulative_cap"] == orchestrator.R2_BUDGET_CAP
    assert manifest["budget"]["cumulative_over_cap"] is False
    assert "exceed the global cap" not in caplog.text

    # Σ>144 → cumulative_over_cap + 显式 warning 披露（纪律条款：不阻断，绝不静默拆窗）
    over_rows = [{"task_id": f"t{index:03d}", "seed": 1, "goal": "g"} for index in range(orchestrator.R2_BUDGET_CAP + 1)]
    view.results_path.write_text("".join(json.dumps(row) + "\n" for row in over_rows), encoding="utf-8")
    caplog.clear()
    view._write_manifest()
    manifest = json.loads(view.manifest_path.read_text(encoding="utf-8"))
    assert manifest["budget"]["cumulative_hercules_used"] == orchestrator.R2_BUDGET_CAP + 1
    assert manifest["budget"]["cumulative_over_cap"] is True
    assert "exceed the global cap" in caplog.text


# ---------------------------------------------------------------------------------------------
# T3 — flatten 过滤器（纯单元）
# ---------------------------------------------------------------------------------------------

#: T3a 的固定合成树（含 role=button 节点、裸 md div、无 md 节点、死分支 role-only 节点、
#: 长串截断、父 name 继承——golden 由迁移前的 r3 实现原样产出）。
GOLDEN_TREE: dict[str, Any] = {
    "role": "WebArea",
    "name": "email-inbox",
    "children": [
        {
            "role": "generic",
            "tag": "div",
            "name": "Inbox from Tisha at 09:12  ",
            "md": 11,
            "children": [
                {"role": "generic", "tag": "div", "md": 12},
                {"role": "link", "tag": "a", "name": "Tisha", "md": 13},
                {"tag": "span", "class": "star", "name": "Tisha", "md": 14},
            ],
        },
        {"role": "button", "tag": "button", "name": "Archive", "md": 15},
        {"role": "textbox", "name": "Body", "md": 16, "value": "hi " * 80},
        {"tag": "select", "md": 17, "options": ["a", "b"]},
        {
            "role": "dialog",
            "modal": True,
            "name": "Confirm",
            "children": [
                {"role": "button", "name": "OK", "md": 18},
            ],
        },
        {"tag": "p", "name": "plain paragraph", "md": 19},
        {"role": "link", "name": "no md at all"},
        {"tag": "textarea", "md": 20, "placeholder": "write"},
        {"tag": "input", "md": 21, "type": "checkbox", "checked": True, "clickable": True},
        {"tag": "img", "md": 22, "title": "banner"},
        {"role": "option", "md": 23, "name": "opt", "id": "strip-me", "for": "x", "level": 2, "empty": "", "nul": None},
        {"tag": "a", "name": "read  more   about\n  grids", "md": 24},
        {"tag": "button", "name": "L" * 320, "md": 25},
        {"tag": "input", "md": 26, "value": "v" * 320},
    ],
}

#: r3 golden：迁移前用现实现（get_interactive_elements.py L45-129 原文）对 GOLDEN_TREE 产出并
#: 固化（dev_runs/r4_impl/capture_golden.py，gitignore 区）。allowed_keys 增 class 不影响它——
#: off 节点永不携带 class 键。
GOLDEN_OFF_JSON = (
    '[{"md":13,"tag":"a","role":"link","name":"Tisha"},'
    '{"md":15,"tag":"button","role":"button","name":"Archive"},'
    '{"md":17,"tag":"select","name":"email-inbox","options":["a","b"]},'
    '{"md":20,"tag":"textarea","name":"email-inbox","placeholder":"write"},'
    '{"md":21,"tag":"input","name":"email-inbox","type":"checkbox","clickable":true,"checked":true},'
    '{"md":24,"tag":"a","name":"read more about grids"},'
    '{"md":25,"tag":"button","name":"' + "L" * 300 + '...[truncated]"},'
    '{"md":26,"tag":"input","name":"email-inbox","value":"' + "v" * 300 + '...[truncated]"}]'
)


def _flatten(tree: dict[str, Any], *, extended: bool, max_nodes: int = 150) -> list[dict[str, Any]]:
    from testzeus_hercules.core.tools.get_interactive_elements import (
        flatten_interactive_nodes,
    )

    return flatten_interactive_nodes(tree, extended=extended, max_nodes=max_nodes)


def test_t3a_off_mode_golden_is_byte_identical() -> None:
    """(a) off 模式 golden——输出与迁移前 r3 实现逐字节一致（含死分支：role-only 节点全不入表）。"""
    from testzeus_hercules.core.tools.get_interactive_elements import (
        compact_interactive_node,
        compact_value,
    )

    assert compact_value("  a   b  ") == "a b"  # 迁移后的 compact 仍同一实现
    assert "class" in compact_interactive_node({"md": 1, "class": "star"})  # MF-1：允许键含 class

    result = _flatten(GOLDEN_TREE, extended=False)
    assert json.dumps(result, separators=(",", ":")) == GOLDEN_OFF_JSON
    assert all("class" not in entry for entry in result)  # off 节点永不携带 class 键（golden 不受 MF-1 影响）


def test_t3b_extended_collects_identified_div_span_li_only() -> None:
    """(b) extended 收录：带 identity 的 div/span/li 入表；无 identity 的裸 md div 不收录。"""
    tree: dict[str, Any] = {
        "children": [
            {"md": 1, "tag": "div", "name": "Tisha"},
            {"md": 2, "tag": "span", "class": "star"},  # 无 name，class 即 identity
            {"md": 3, "tag": "li", "id": "x"},
            {"md": 4, "tag": "div"},  # 无任何 identity 键 → 不收录
            {"md": 5, "tag": "p", "name": "not extended"},  # p 不在 EXTENDED_TAGS
            {"md": 6, "tag": "tr", "title": "row-title"},
        ]
    }
    rows = _flatten(tree, extended=True)
    assert [row["md"] for row in rows] == [1, 2, 3, 6]
    assert rows[1]["class"] == "star"  # compact 未剥离 class（MF-1）


def test_t3c_extended_role_whitelist() -> None:
    """(c) extended 的 role 白名单（匹配现管线的 ``role`` 键）：row/cell/listitem/img 收录。"""
    tree: dict[str, Any] = {
        "children": [
            {"md": 1, "tag": "div", "role": "row"},
            {"md": 2, "tag": "td", "role": "cell"},
            {"md": 3, "role": "listitem"},  # 无 tag 也收录（role 命中）
            {"md": 4, "tag": "img", "role": "img"},
            {"md": 5, "tag": "div", "role": "grid"},  # 白名单外
        ]
    }
    rows = _flatten(tree, extended=True)
    assert [row["md"] for row in rows] == [1, 2, 3, 4]
    # off 模式同一棵树：role 键不查（仍死分支），全部不入表（r3 复现）
    assert _flatten(tree, extended=False) == []


def test_t3d_extended_cap_at_150_with_truncation_marker(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """(d) extended 上限：>150 收录截断 + [R2G_MD_TRUNCATED] 留痕；前 150 条保序。"""
    tree: dict[str, Any] = {"children": [{"md": index, "tag": "button", "name": f"b{index}"} for index in range(1, 161)]}
    caplog.set_level(logging.WARNING, logger="testzeus_hercules.utils.logger")
    rows = _flatten(tree, extended=True, max_nodes=150)
    assert len(rows) == 150
    assert [row["md"] for row in rows] == list(range(1, 151))  # 文档序保序，截掉尾部
    assert "[R2G_MD_TRUNCATED]" in caplog.text and "capped at 150" in caplog.text

    # 自定义上限同语义（可测性），哨兵节点永不写入 JSON
    caplog.clear()
    rows = _flatten(tree, extended=True, max_nodes=3)
    assert [row["md"] for row in rows] == [1, 2, 3]
    assert "[R2G_MD_TRUNCATED]" in caplog.text


def test_t3e_off_mode_never_caps() -> None:
    """(e) off 模式 151+ 节点不截断（r3 现状，无上限）。"""
    tree: dict[str, Any] = {"children": [{"md": index, "tag": "button", "name": f"b{index}"} for index in range(1, 161)]}
    rows = _flatten(tree, extended=False)
    assert len(rows) == 160
    assert [row["md"] for row in rows] == list(range(1, 161))


def test_t3f_compact_keeps_class_mf1() -> None:
    """(f) MF-1：star/trash 同 name 图标经 compact 后仍以 class 互异（不被剥成同形）。"""
    tree: dict[str, Any] = {
        "children": [
            {"md": 11, "tag": "span", "name": "Caralie", "class": "star"},
            {"md": 12, "tag": "span", "name": "Caralie", "class": "trash"},
        ]
    }
    rows = _flatten(tree, extended=True)
    assert rows == [
        {"md": 11, "tag": "span", "name": "Caralie", "class": "star"},
        {"md": 12, "tag": "span", "name": "Caralie", "class": "trash"},
    ]
    assert rows[0] != rows[1]  # 不因 compact 同形


# ---------------------------------------------------------------------------------------------
# T4 — 真浏览器注入/终表验证（本机回环 + vendored 页；chromium 缺失时 skip）
# ---------------------------------------------------------------------------------------------

try:  # 环境缺 playwright 时整体 skip，而不是收集期报错
    from playwright.async_api import async_playwright
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_IMPORTED = True
except Exception:  # pragma: no cover - defensive
    _PLAYWRIGHT_IMPORTED = False


def _chromium_available() -> bool:
    if os.environ.get("SKIP_BROWSER_TESTS") == "1" or not _PLAYWRIGHT_IMPORTED:
        return False
    try:
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            instance.close()
        return True
    except Exception:  # pragma: no cover - environment dependent
        return False


def _input_fields_flatten(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """``get_input_fields`` 的表内过滤（form_elements + 父 name/title 继承），原样复刻其逻辑。"""
    form_elements = {"input", "label", "select", "textarea", "button", "fieldset", "legend", "datalist", "output", "option", "optgroup"}

    def flatten(node: dict[str, Any], parent_name: str = "", parent_title: str = "") -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        if "children" in node:
            current_name = node.get("name", parent_name)
            current_title = node.get("title", parent_title)
            for child in node["children"]:
                if "name" not in child and current_name:
                    child["name"] = current_name
                if "title" not in child and current_title:
                    child["title"] = current_title
                elements.extend(flatten(child, current_name, current_title))
        if "md" in node and node.get("tag", "").lower() in form_elements:
            new_node = dict(node)
            new_node.pop("children", None)
            elements.append(new_node)
        return elements

    return flatten(tree)


class _BrowserProbe:
    """真浏览器探测：打开 seeded 页 → auto-start → do_get_accessibility_info（A 的注入层假设验证器）。"""

    def __init__(self, server: MiniWobServer, project_root: Path) -> None:
        self.base_url = server.base_url
        self.project_root = project_root

    async def _fetch(self, subdomain: str, seed: int, *, only_input_fields: bool) -> dict[str, Any] | None:
        from testzeus_hercules.utils.get_detailed_accessibility_tree import (
            do_get_accessibility_info,
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(f"{self.base_url}/miniwob/{subdomain}.html?r2g_seed={seed}&r2g_ms=240000", wait_until="load")
                await page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=20000)
                await page.wait_for_function("() => core.ept0 != null", timeout=20000)  # auto-start 完成（实现说明 2）
                return await do_get_accessibility_info(page, only_input_fields=only_input_fields)
            finally:
                await browser.close()

    def fetch(self, subdomain: str, seed: int = 42, *, only_input_fields: bool = False) -> dict[str, Any] | None:
        return asyncio.run(self._fetch(subdomain, seed, only_input_fields=only_input_fields))


@pytest.fixture()
def browser_probe(miniwob_server: MiniWobServer, tmp_path: Path, config_key: Any) -> Iterator[_BrowserProbe]:
    """config 日志目录指 tmp（do_get_accessibility_info 的 json dump 落 tmp）。"""
    config_key("PROJECT_SOURCE_ROOT", str(tmp_path))
    yield _BrowserProbe(miniwob_server, tmp_path)


@pytest.mark.skipif(not _chromium_available(), reason="no local headless chromium for playwright (uv run playwright install chromium)")
class TestT4RealBrowser:
    def test_email_inbox_extended_table_carries_rows_and_icons(self, browser_probe: _BrowserProbe, config_key: Any) -> None:
        """extended：.email-thread 行与 star/trash 图标入终表、全带 md、star/trash class 互异（MF-1）。"""
        config_key("MD_INTERACTIVE_EXTENDED", "true")
        info = browser_probe.fetch("email-inbox")
        assert isinstance(info, dict)
        rows = _flatten(info, extended=True)
        assert rows, "extended 终表为空——注入层假设破裂（§1.3 降级授权触发判据）"
        thread_rows = [row for row in rows if "email-thread" in str(row.get("class", ""))]
        assert len(thread_rows) >= 1
        icon_classes = {str(row.get("class", "")) for row in rows if str(row.get("class", "")) in {"star", "trash"}}
        assert len([row for row in rows if str(row.get("class", "")) in {"star", "trash"}]) >= 2
        assert icon_classes == {"star", "trash"}  # star/trash class 值互异（锁定 MF-1：class 未被 compact 剥离）
        assert all("md" in row for row in rows)

    def test_email_inbox_off_table_has_none_of_them(self, browser_probe: _BrowserProbe, config_key: Any) -> None:
        """off：行/图标零出现且全表无 class 键（r3 复现 = executor 自述的 empty 表形态）。"""
        config_key("MD_INTERACTIVE_EXTENDED", None)
        info = browser_probe.fetch("email-inbox")
        assert isinstance(info, dict)
        rows = _flatten(info, extended=False)
        assert all("class" not in row for row in rows)
        assert not any("email-thread" in json.dumps(row) for row in rows)

    def test_find_greatest_extended_table_carries_the_cards(self, browser_probe: _BrowserProbe, config_key: Any) -> None:
        """extended（find-greatest）：≥4 个 .card 条目入表、name 为数字文本。"""
        config_key("MD_INTERACTIVE_EXTENDED", "true")
        info = browser_probe.fetch("find-greatest")
        assert isinstance(info, dict)
        rows = _flatten(info, extended=True)
        numeric = [row for row in rows if str(row.get("name", "")).strip().isdigit()]
        assert len(numeric) >= 4
        assert all("md" in row for row in numeric)

    def test_input_fields_extended_carry_class_anchor(self, browser_probe: _BrowserProbe, config_key: Any) -> None:
        """§1.3 披露锚（book-flight 静态输入）：extended 产物含 class 字段、off 不含（get_input_fields 语义）。"""
        config_key("MD_INTERACTIVE_EXTENDED", "true")
        info = browser_probe.fetch("book-flight", only_input_fields=True)
        assert isinstance(info, dict)
        fields = _input_fields_flatten(info)
        classes = [str(field.get("class", "")) for field in fields]
        assert any("flight-input" in cls for cls in classes), classes  # 实测值形如 "flight-input ui-autocomplete-input"

        config_key("MD_INTERACTIVE_EXTENDED", None)
        info = browser_probe.fetch("book-flight", only_input_fields=True)
        assert isinstance(info, dict)
        fields = _input_fields_flatten(info)
        assert fields
        assert all("class" not in field for field in fields)


# ---------------------------------------------------------------------------------------------
# T5 — verify 路由
# ---------------------------------------------------------------------------------------------


def _hercules_stub() -> SimpleHercules:
    return SimpleHercules.__new__(SimpleHercules)  # 绕过 init（r3 T2 同法）


def test_t5a_verify_route_triggers_once(config_key: Any, caplog: pytest.LogCaptureFixture) -> None:
    """(a)+(b) flag on + terminate=yes + is_passed=True + verify_rounds=0 → "verify"；gate 节点产出恰为 spec §2.2 字典。"""
    config_key("VERIFY_BEFORE_DONE", "true")
    instance = _hercules_stub()
    state: dict[str, Any] = {"terminate": "yes", "is_passed": True, "verify_rounds": 0}
    assert instance._route_after_planner(state) == "verify"

    caplog.set_level(logging.WARNING, logger="testzeus_hercules.utils.logger")
    result = instance._verify_gate_node(state)
    assert result == {
        "next_step": _VERIFY_STEP,
        "target_helper": "browser",
        "terminate": "no",
        "is_assert": False,
        "verify_rounds": 1,
    }
    assert "[R2G_VERIFY]" in caplog.text and "verify_rounds=1" in caplog.text


def test_t5b_hard_cap_second_terminate_passes(config_key: Any) -> None:
    """(c) verify_rounds=1 + 同条件 → "end"（每场景至多 1 次核验，无循环面）。"""
    config_key("VERIFY_BEFORE_DONE", "true")
    instance = _hercules_stub()
    state: dict[str, Any] = {"terminate": "yes", "is_passed": True, "verify_rounds": 1}
    assert instance._route_after_planner(state) == "end"


def test_t5c_failed_termination_is_never_verified(config_key: Any) -> None:
    """(d) is_passed=False + terminate=yes → "end"（不核验）。"""
    config_key("VERIFY_BEFORE_DONE", "true")
    instance = _hercules_stub()
    for rounds in (0, 1):
        assert instance._route_after_planner({"terminate": "yes", "is_passed": False, "verify_rounds": rounds}) == "end"


def test_t5d_flag_off_is_r3_parity(config_key: Any) -> None:
    """(e) flag off + is_passed=True → "end"（r3 复现）；assertion 分支不受影响。"""
    config_key("VERIFY_BEFORE_DONE", None)
    instance = _hercules_stub()
    assert instance._route_after_planner({"terminate": "yes", "is_passed": True, "verify_rounds": 0}) == "end"
    assert instance._route_after_planner({"terminate": "no", "is_assert": True, "target_helper": "Not_Applicable"}) == "assertion"
    assert instance._route_after_planner({"terminate": "no", "is_assert": False, "target_helper": "browser"}) == "executor"


# ---------------------------------------------------------------------------------------------
# T6 — verify 全链（stub planner/executor 序列）
# ---------------------------------------------------------------------------------------------


def _run_verify_graph(monkeypatch: pytest.MonkeyPatch, config_key: Any, planner_script: list[dict[str, Any]]) -> dict[str, Any]:
    """以 stub planner/executor 建图并跑完整链；返回节点执行轨迹与各次执行的状态快照。"""
    order: list[str] = []
    calls: dict[str, list[dict[str, Any]]] = {"planner": [], "executor": [], "verify": []}
    script = list(planner_script)
    original_verify = SimpleHercules.__dict__["_verify_gate_node"]

    def planner_node(state: dict[str, Any]) -> dict[str, Any]:
        order.append("planner")
        calls["planner"].append(dict(state))
        return dict(script.pop(0)) if script else {"terminate": "yes", "is_passed": False, "next_step": "", "final_response": "x"}

    def executor_node(state: dict[str, Any]) -> dict[str, Any]:
        order.append("executor")
        calls["executor"].append(dict(state))
        return {"messages": state.get("messages", [])}

    def assertion_node(state: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - 本链不应到达
        raise AssertionError("assertion node must not run in the verify chain")

    def verify_spy(state: dict[str, Any]) -> dict[str, Any]:
        order.append("verify")
        calls["verify"].append(dict(state))
        return original_verify(_hercules_stub(), state)

    monkeypatch.setattr(SimpleHercules, "_planner_node", staticmethod(planner_node))
    monkeypatch.setattr(SimpleHercules, "_executor_node", staticmethod(executor_node))
    monkeypatch.setattr(SimpleHercules, "_assertion_node", staticmethod(assertion_node))
    monkeypatch.setattr(SimpleHercules, "_verify_gate_node", staticmethod(verify_spy))

    instance = _hercules_stub()
    graph = SimpleHercules._build_graph(instance)
    config_key("VERIFY_BEFORE_DONE", "true")

    initial: dict[str, Any] = {
        "messages": [],
        "task": "t",
        "plan": "",
        "next_step": "",
        "target_helper": "browser",
        "terminate": "no",
        "final_response": "",
        "is_assert": False,
        "assert_summary": "",
        "is_passed": False,
        "planner_turn": 0,
        "step_token_log": [],
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost": 0.0,
        "cost_available": False,
        "step_timings": [],
        "completed_step_signatures": [],
        "last_helper_response": "",
        "current_url": "",
        "verify_rounds": 0,
    }
    final = asyncio.run(graph.ainvoke(initial, config={"recursion_limit": 100}))
    return {"order": order, "calls": calls, "final": final}


def test_t6_verify_chain_runs_the_forced_round_then_ends(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """planner 首轮 terminate=yes/is_passed=true、二轮同值 → planner→verify→executor→planner→END；
    核验步经 executor 路径执行且 next_step == _VERIFY_STEP；verify_rounds 全程 ≤1（实现说明 1）。"""
    plan_yes = {"terminate": "yes", "is_passed": True, "next_step": "done", "final_response": "done"}
    outcome = _run_verify_graph(monkeypatch, config_key, [plan_yes, dict(plan_yes)])
    assert outcome["order"] == ["planner", "verify", "executor", "planner"]
    calls = outcome["calls"]
    assert len(calls["verify"]) == 1  # 恰一次强制核验
    assert len(calls["executor"]) == 1
    assert calls["executor"][0]["next_step"] == _VERIFY_STEP  # 核验步内容进 executor 状态
    assert calls["executor"][0]["terminate"] == "no"
    assert calls["verify"][0]["verify_rounds"] == 0  # 进入 verify 前尚未核验过
    assert outcome["final"]["verify_rounds"] == 1  # 全程从不大于 1


def test_t6_second_terminate_after_verify_ends_without_reverify(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """核验后 planner 改判 is_passed=false → 直接 END；不出现第二次 verify。"""
    outcome = _run_verify_graph(
        monkeypatch,
        config_key,
        [
            {"terminate": "yes", "is_passed": True, "next_step": "done", "final_response": "done"},
            {"terminate": "yes", "is_passed": False, "next_step": "", "final_response": "actually failed"},
        ],
    )
    calls = outcome["calls"]
    assert len(calls["verify"]) == 1
    assert len(calls["executor"]) == 1
    assert outcome["final"]["verify_rounds"] == 1
    assert outcome["final"]["is_passed"] is False


# ---------------------------------------------------------------------------------------------
# T7 — 扫描器与 metrics
# ---------------------------------------------------------------------------------------------


def _t7_view(tmp_path: Path, tasks: list[dict[str, Any]]) -> tuple[orchestrator.Orchestrator, orchestrator.Cell]:
    view = orchestrator.Orchestrator("miniwob-r4", stage="pilot", exp_root=tmp_path / "dev_runs", tasks=tasks)
    cell = orchestrator.plan_cells("miniwob-r4", "pilot", tasks=tasks)[0]
    view.rewards_path.parent.mkdir(parents=True, exist_ok=True)
    return view, cell


def _t7_reward_record(cell: orchestrator.Cell, **overrides: Any) -> dict[str, Any]:
    payload = {"path": f"/miniwob/{cell.subdomain}.html", "seed": str(cell.seed), "reward": 1, "raw": 1, "done": True, "reason": "", "received_at": "2026-09-22T00:00:30+00:00"}
    payload.update(overrides)
    return payload


def _t7_write_rewards(view: orchestrator.Orchestrator, *records: dict[str, Any]) -> None:
    view.rewards_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_t7a_timeout_with_zero_navigations_is_flagged(tmp_path: Path, tasks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) ① 合成 timeout 行 + nav==0 → flagged（invalid_reason 恒 None，status 不改写）。"""
    view, cell = _t7_view(tmp_path, tasks)
    log_path = view._stdout_log_path(cell)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("# run_id: x\n", encoding="utf-8")  # 0 次任务 URL 导航
    _t7_write_rewards(view, _t7_reward_record(cell, raw=-1, reward=-1, reason="timed out"))  # 页面超时形态

    monkeypatch.setattr(
        orchestrator.runner_module,
        "run_feature",
        lambda *args, **kwargs: orchestrator.runner_module.RunResult(
            run_id=cell.run_id, status="timeout", passed=False, duration_s=600.0, cost_usd=None, total_tokens=None, junit_xml=None, failure_message=None, run_dir="/tmp/run"
        ),
    )
    monkeypatch.setattr(orchestrator, "fetch_reward", lambda *args, **kwargs: _t7_reward_record(cell, raw=-1, reward=-1, reason="timed out"))
    row = view._execute_cell(cell, goal="g", started_at="2026-09-22T00:00:00+00:00", started=0.0)
    assert row["status"] == "timeout"  # 判定一字不改
    assert row["task_url_navigations"] == 0 and row["flagged"] is True and row["invalid_reason"] is None
    assert row["reward_records"] == 1  # 零事件标注不适用（页面有裁决记录）


def test_t7b_offseed_record_in_window_flags_out_window_does_not(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """(b) ② offseed 记录入窗 → off_seed_navigation；出窗 → 不标。match_records 窗口语义直测。"""
    view, cell = _t7_view(tmp_path, tasks)
    prefix = orchestrator.BEACON_PATH_PREFIX.format(subdomain=cell.subdomain)
    records = [{"ts": "2026-09-22T00:00:10+00:00", "path": prefix}]
    window = ("2026-09-22T00:00:00+00:00", "2026-09-22T00:01:00+00:00")

    assert orchestrator.match_records(prefix, orchestrator._epoch_window(*window), records) is True
    assert orchestrator.match_records(prefix, orchestrator._epoch_window("2026-09-22T00:01:10+00:00", "2026-09-22T00:02:00+00:00"), records) is False
    assert orchestrator.match_records("/miniwob/other.html", orchestrator._epoch_window(*window), records) is False
    assert orchestrator.match_records(prefix, orchestrator._epoch_window(None, None), [{"path": prefix}]) is True  # 无 ts 记安全侧

    # 入窗：_scan_cell 合并 off_seed_navigation（reward=1 → 零事件标注不适用）
    offseed = view.exp_dir / OFFSEED_FILENAME
    offseed.write_text(json.dumps({"ts": "2026-09-22T00:00:10+00:00", "path": prefix}) + "\n", encoding="utf-8")
    _t7_write_rewards(view, _t7_reward_record(cell))
    scan = view._scan_cell(cell, started_at=window[0], finished_at=window[1])
    assert scan.flagged is True and scan.invalid_reason is None
    assert scan.flag_reasons == (orchestrator.REASON_OFF_SEED,)

    # 出窗：不标（该窗仍含合法奖励记录 → 零事件标注同样不适用；r3 回归：干净行零标注）
    scan = view._scan_cell(cell, started_at="2026-09-22T00:00:20+00:00", finished_at="2026-09-22T00:01:00+00:00")
    assert scan.flagged is False and scan.flag_reasons == () and scan.reward_records == 1


def test_t7c_zero_reward_events_vs_never_started(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """(c) ③ reward_records==0 → zero_reward_events；epstart 零命中 → episode_never_started（替代不叠加）。"""
    view, cell = _t7_view(tmp_path, tasks)
    window = ("2026-09-22T00:00:00+00:00", "2026-09-22T00:01:00+00:00")
    prefix = orchestrator.BEACON_PATH_PREFIX.format(subdomain=cell.subdomain)

    # 零事件 + epstart 在窗 → zero_reward_events（已启动无奖励 = 上报缺口）
    (view.exp_dir / EPSTART_FILENAME).write_text(json.dumps({"ts": "2026-09-22T00:00:05+00:00", "path": prefix, "seed": str(cell.seed)}) + "\n", encoding="utf-8")
    scan = view._scan_cell(cell, started_at=window[0], finished_at=window[1])
    assert scan.reward_records == 0
    assert scan.flag_reasons == (orchestrator.REASON_ZERO_REWARD,)

    # 零事件 + epstart 零命中 → episode_never_started（注册缺口），两理由绝不叠加
    (view.exp_dir / EPSTART_FILENAME).write_text("", encoding="utf-8")
    scan = view._scan_cell(cell, started_at=window[0], finished_at=window[1])
    assert scan.flag_reasons == (orchestrator.REASON_EP_NEVER_STARTED,)
    assert orchestrator.REASON_ZERO_REWARD not in scan.flag_reasons
    assert scan.flagged is True and scan.invalid_reason is None  # 全部 flagged-only


def test_t7d_clean_cell_stays_unflagged(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """(d) 干净行零标注（r3 回归）：1 次导航 + 1 条合法奖励 + 无 beacon 记录。"""
    view, cell = _t7_view(tmp_path, tasks)
    log_path = view._stdout_log_path(cell)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[2026-09-22 00:00:10] INFO {{open_url.py:29}} - Opening URL: http://127.0.0.1:8462/miniwob/{cell.subdomain}.html?r2g_seed={cell.seed}&r2g_ms=240000 (force_new_tab=False)\n",
        encoding="utf-8",
    )
    _t7_write_rewards(view, _t7_reward_record(cell))
    scan = view._scan_cell(cell, started_at="2026-09-22T00:00:00+00:00", finished_at="2026-09-22T00:01:00+00:00")
    assert scan == orchestrator.CellScan(task_url_navigations=1, reward_records=1, flagged=False, invalid_reason=None, flag_reasons=())


def test_t7e_zero_reward_cells_list_and_truncation() -> None:
    """(e) zero_reward_cells 清单按 task_id 排序、>50 截断并计数（复用 invalid_cells 样式）。"""
    rows = [{"task_id": f"t{index:03d}", "seed": 1, "status": "timeout", "official_passed": False, "reward_records": 0} for index in range(60)]
    rows += [{"task_id": f"t{index:03d}", "seed": 1, "status": "official_passed", "official_passed": True, "reward_records": 2} for index in range(60, 63)]
    tasks = [{"task_id": f"t{index:03d}"} for index in range(63)]
    summary = metrics_module.summarize(rows, tasks=tasks, exp_id="t7e")
    assert len(summary.zero_reward_cells) == 50 and summary.zero_reward_cells_total == 60
    assert [entry["task_id"] for entry in summary.zero_reward_cells] == sorted(entry["task_id"] for entry in summary.zero_reward_cells)
    assert summary.zero_reward_cells[0] == {"task_id": "t000", "seed": 1, "status": "timeout"}
    assert summary.as_dict()["zero_reward_cells_total"] == 60
    # overall/clean/invalid_cells 零改动（回归）
    assert summary.overall.passed == 3 and summary.overall.total == 63
    assert summary.clean is not None and summary.clean.total == 63 and summary.invalid_cells == []


def test_t7f_r3_shape_replay_exactly_six_zero_event_cells(tasks: list[dict[str, Any]], tmp_path: Path) -> None:
    """(f) r3 数据形态回放（analysis-r3 §4.5）：125 格中恰好 NR3+T3 的 6 形态命中零事件标注。"""
    subdomain_to_task = {}
    for task in tasks:
        subdomain_to_task[str(task["subdomain"])] = str(task["task_id"])
    zero_task_ids = {subdomain_to_task[subdomain] for subdomain in ZERO_EVENT_SUBDOMAINS}
    assert len(zero_task_ids) == 6

    rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task["task_id"])
        zero = task_id in zero_task_ids
        rows.append(
            {
                "task_id": task_id,
                "seed": 1,
                "status": "timeout" if zero else "official_passed",
                "official_passed": not zero,
                "reward_records": 0 if zero else 1,
            }
        )
    summary = metrics_module.summarize(rows, tasks=tasks, exp_id="t7f")
    assert summary.zero_reward_cells_total == 6
    assert {entry["task_id"] for entry in summary.zero_reward_cells} == zero_task_ids
    assert {entry["status"] for entry in summary.zero_reward_cells} == {"timeout"}
    # 其余 119 形态零命中
    assert len(summary.zero_reward_cells) == 6


# ---------------------------------------------------------------------------------------------
# T8 — 补丁与服务端点
# ---------------------------------------------------------------------------------------------


def _post_raw(url: str, body: bytes, content_type: str = "application/json") -> int:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:  # noqa: S310 - loopback only
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_t8a_off_beacon_reproduces_the_r3_bytes(html_root: Path) -> None:
    """(a) offseed_beacon=False 输出 == r3 版函数输出（golden 字节：原文 + A + B + 可选 cue）。"""
    source = (html_root / "core" / "core.js").read_bytes()
    r3_plain = source + f"\n{AUTO_START_PATCH}\n{REWARD_HOOK_PATCH}\n".encode("utf-8")
    assert patch_core_js(source) == r3_plain
    assert patch_core_js(source, terminal_cue=False, single_start=False, offseed_beacon=False) == r3_plain
    # 带 cue/single 组合时 offseed_beacon=False 也与不含 beacon 的构造逐字节一致（纯追加语义）
    assert patch_core_js(source, terminal_cue=True, single_start=True, offseed_beacon=False) == patch_core_js(source, terminal_cue=True, single_start=True)
    assert "r4 integrity beacons" not in patch_core_js(source).decode("utf-8")
    assert "/__r2g_offseed" not in patch_core_js(source, terminal_cue=True).decode("utf-8")


def test_t8b_beacon_is_a_pure_append(html_root: Path) -> None:
    """(b) True → 追加段含 /__r2g_offseed 与 epstart，且仍为纯 append（前缀字节不变，序在 cue 之后）。"""
    source = (html_root / "core" / "core.js").read_bytes()
    without = patch_core_js(source, terminal_cue=True, single_start=True)
    with_beacon = patch_core_js(source, terminal_cue=True, single_start=True, offseed_beacon=True)
    assert with_beacon.startswith(without)  # 纯 append
    assert with_beacon[len(without) :].decode("utf-8").strip() == INTEGRITY_BEACON_PATCH.strip()
    text = INTEGRITY_BEACON_PATCH
    assert "/__r2g_" in text and 'beep("offseed")' in text and 'beep("epstart"' in text
    # 追加序：auto_start → REWARD_HOOK → terminal_cue（如有）→ 本补丁
    full = with_beacon.decode("utf-8")
    assert full.index("__R2G_REWARD_HOOK__") < full.index("__R2G_TERMINAL_CUE__") < full.index("r4 integrity beacons")


def test_t8c_beacon_endpoints_write_jsonl_and_answer_204(miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """(c) POST 两端点 → jsonl 各一行、字段齐、204；坏 body 不 500（尽力解析、空串兜底）。"""
    from tests.record2gherkin.benchmark.conftest import http_get_json

    base = miniwob_server.base_url
    assert _post_raw(f"{base}{OFFSEED_PATH}", json.dumps({"path": "/miniwob/click-test.html"}).encode()) == 204
    assert _post_raw(f"{base}{EPSTART_PATH}", json.dumps({"path": "/miniwob/click-test.html", "seed": "42"}).encode()) == 204
    assert _post_raw(f"{base}{OFFSEED_PATH}", b"not json at all") == 204  # 坏 body：不 500
    assert _post_raw(f"{base}{EPSTART_PATH}", b"", content_type="text/plain") == 204

    offseed_lines = [json.loads(line) for line in (reward_file.parent / OFFSEED_FILENAME).read_text(encoding="utf-8").splitlines() if line.strip()]
    epstart_lines = [json.loads(line) for line in (reward_file.parent / EPSTART_FILENAME).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(offseed_lines) == 2
    assert offseed_lines[0]["path"] == "/miniwob/click-test.html" and offseed_lines[0]["ts"]
    assert offseed_lines[1]["path"] == ""  # 坏 body → 空串
    assert len(epstart_lines) == 2
    assert epstart_lines[0]["seed"] == "42" and epstart_lines[0]["path"] == "/miniwob/click-test.html"
    assert epstart_lines[1]["seed"] == ""  # 空 body → 空串

    # beacon 文件与 rewards.jsonl 同目录（= exp_root），/healthz 形状不变
    code, health = http_get_json(f"{base}/healthz")
    assert code == 200 and set(health) == {"served_root", "patched", "rewards"}
    assert (reward_file.parent / OFFSEED_FILENAME).parent == reward_file.parent


def test_t8d_reward_endpoint_regression(miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """(d) 奖励端点行为零变化（回归）：合法 payload → 200 + 一行；坏 payload → 400。"""
    from tests.record2gherkin.benchmark.conftest import http_post_json

    base = miniwob_server.base_url
    status, payload = http_post_json(
        f"{base}/__r2g_reward",
        {"path": "/miniwob/click-test.html", "seed": "5", "reward": 1, "raw": 1, "done": True, "reason": "", "ts": "2026-01-01T00:00:00Z"},
    )
    assert status == 200 and payload["ok"] is True
    status, payload = http_post_json(f"{base}/__r2g_reward", {"path": "", "seed": ""})
    assert status == 400
    lines = [json.loads(line) for line in reward_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0]["seed"] == "5"


# ---------------------------------------------------------------------------------------------
# T9 — 工具输出 parity
# ---------------------------------------------------------------------------------------------


class _T9Manager:
    async def wait_for_page_and_frames_load(self) -> None:
        return None

    async def get_current_page(self) -> object:
        return object()

    async def wait_for_load_state_if_enabled(self, page: Any, state: str | None = None) -> None:
        return None


async def _noop_wait(page: Any, seconds: int) -> None:
    return None


def _noop_event(*args: Any, **kwargs: Any) -> None:
    return None


def _stub_tool(monkeypatch: pytest.MonkeyPatch, tree: dict[str, Any] | None) -> None:
    # 包命名空间的 get_interactive_elements 属性被 star-import 的同名函数遮蔽，须按模块对象取
    tool_module = importlib.import_module("testzeus_hercules.core.tools.get_interactive_elements")

    async def fake_info(page: Any, only_input_fields: bool = False) -> dict[str, Any] | None:
        return tree

    monkeypatch.setattr(tool_module, "PlaywrightManager", _T9Manager)
    monkeypatch.setattr(tool_module, "wait_for_non_loading_dom_state", _noop_wait)
    monkeypatch.setattr(tool_module, "do_get_accessibility_info", fake_info)
    monkeypatch.setattr(tool_module, "add_event", _noop_event)


def test_t9_tool_output_matches_the_r3_golden_off(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """(a) off 模式工具 JSON 与 r3 golden 一致（allowed_keys 增 class 不影响——off 无节点带该键）。"""
    from testzeus_hercules.core.tools.get_interactive_elements import (
        get_interactive_elements,
    )

    config_key("MD_INTERACTIVE_EXTENDED", None)
    _stub_tool(monkeypatch, GOLDEN_TREE)
    assert asyncio.run(get_interactive_elements()) == GOLDEN_OFF_JSON


def test_t9_tool_output_empty_table_copy(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """(a 附) 空表输出与 r3 逐字节一致（"[]"）。

    实现说明：``return extracted_data or "Its Empty, try something else"`` 的文案分支在 r3 即为
    不可达死代码（``json.dumps`` 恒返回非空串，空表实际返回 "[]"）；r4 原样保留、未触碰。
    """
    from testzeus_hercules.core.tools.get_interactive_elements import (
        get_interactive_elements,
    )

    config_key("MD_INTERACTIVE_EXTENDED", None)
    _stub_tool(monkeypatch, {"children": [{"tag": "p", "md": 1, "name": "non-interactive"}]})
    assert asyncio.run(get_interactive_elements()) == "[]"
    _stub_tool(monkeypatch, "not a dict")
    assert asyncio.run(get_interactive_elements()) == "[]"


def test_t9_tool_output_extended_survives_class(monkeypatch: pytest.MonkeyPatch, config_key: Any) -> None:
    """(b) extended 模式含新条目且 class 字段在终表存活（MF-1 的工具层锚）。

    收录面按 spec §1.2：extended 的 role 命中走 ``role`` 键（死分支复活），identity 键决定
    div/span 收录；compact 只新增放行 ``class``（MF-1 最小改动）——``id`` 仍被剥离。
    """
    from testzeus_hercules.core.tools.get_interactive_elements import (
        get_interactive_elements,
    )

    config_key("MD_INTERACTIVE_EXTENDED", "true")
    _stub_tool(monkeypatch, GOLDEN_TREE)
    extended_json = asyncio.run(get_interactive_elements())
    assert '"class":"star"' in extended_json  # span 图标以 class 入表
    payload = json.loads(extended_json)
    by_md = {entry["md"]: entry for entry in payload}

    inherited = "Inbox from Tisha at 09:12"  # 父 md11 的 name（compact 折叠空白后）
    assert by_md[11] == {"md": 11, "tag": "div", "role": "generic", "name": inherited}  # 行 div（own name；compact 保留 role 键）
    assert by_md[12] == {"md": 12, "tag": "div", "role": "generic", "name": inherited}  # 裸 div（identity 经父链继承）
    assert by_md[14] == {"md": 14, "tag": "span", "name": "Tisha", "class": "star"}  # 图标：class 存活
    assert by_md[16] == {"md": 16, "role": "textbox", "name": "Body", "value": " ".join(["hi"] * 80)}  # role 键复活
    assert by_md[18] == {"md": 18, "role": "button", "name": "OK"}  # 对话框内 role=button
    assert by_md[22] == {"md": 22, "tag": "img", "name": "email-inbox", "title": "banner"}  # img（title 即 identity；name 系根继承）
    assert by_md[23] == {"md": 23, "role": "option", "name": "opt", "level": 2}  # id/for 被剥离：compact 仅放行 class（MF-1 最小改动）
    assert 19 not in by_md  # p 不在 EXTENDED_TAGS，extended 也不收录
