"""D 组浏览器用例（spec §9 用例 16）：真实伺服器 + 真实 vendored 页面，仅本机回环。

(a) ``?r2g_seed=`` 自动开局（证据 = ``core.ept0``/utterance/``EPISODE_MAX_TIME``/无 ``__R2G_START_ERROR``，
**不**用 ``WOB_EPISODE_ID > 0``：开局成功后它仍是 0，review 必改 1.6）；
(b) 同 URL 同 seed 的 utterance 恒等，且预读不产生 reward 记录；
(c) 页内 ``core.endEpisode(1)`` → ``/latest`` 取回 ``raw>0, done=true``；
(d) H1 安全加固（审查报告 §2.V1/V2）：HUD/START 零命中、覆盖层点不到、实例不重开、奖励 POST 完好。

无本地 chromium（或 ``SKIP_BROWSER_TESTS=1``）时整个模块 skip。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from record2gherkin.benchmark import goal_reader
from record2gherkin.benchmark.miniwob_server import MiniWobServer
from tests.record2gherkin.benchmark.conftest import http_get_json, read_jsonl

try:  # 环境缺 playwright 时整体 skip，而不是收集期报错
    from playwright.sync_api import Browser
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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


pytestmark = pytest.mark.skipif(not _chromium_available(), reason="no local headless chromium for playwright (uv run playwright install chromium)")

SUBDOMAIN = "click-test"
SEED = 424242
EPISODE_MS = 240000
EPISODE_READY = "() => !!core.ept0"

#: H1（审查报告 §2.V1/V2）：加固后 agent 的文本视角（``get_page_text`` = ``body.innerText``）零命中这些词。
HUD_LEAK_KEYWORDS = ("reward", "START", "Time left", "Episodes done", "Last 10 average")
#: HUD/覆盖层可见性读取（``missing`` = 从未创建，同样是安全结果）。
HUD_DISPLAY = "() => { const e = document.getElementById('reward-display'); return e ? e.style.display : 'missing'; }"
COVER_DISPLAY = "() => { const e = document.getElementById('sync-task-cover'); return e ? e.style.display : 'missing'; }"


def _assert_no_hud_leak(text: str) -> None:
    """agent 视角零 HUD/START 命中（大小写不敏感：``START`` 与 ``Start`` 都算泄漏）。"""
    lowered = text.lower()
    hits = [keyword for keyword in HUD_LEAK_KEYWORDS if keyword.lower() in lowered]
    assert not hits, f"HUD/START leak in body.innerText: {hits} -> {text!r}"


@pytest.fixture()
def browser() -> Iterator[Browser]:
    """Function-scoped on purpose: a live sync-playwright context keeps the driver's event loop
    marked as running in this thread, and ``read_goal`` opens its *own* context (16(b)) — two
    concurrent sync contexts in one thread are not allowed by playwright."""
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture()
def started_page(browser: Browser, miniwob_server: MiniWobServer) -> Iterator[Any]:
    """A page whose episode was auto-started from the URL seed."""
    page = browser.new_page()
    page.set_default_timeout(20000)
    page.goto(goal_reader.page_url(SUBDOMAIN, port=miniwob_server.port, seed=SEED, episode_ms=EPISODE_MS), wait_until="load")
    page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=20000)
    page.wait_for_function(EPISODE_READY, timeout=20000)
    try:
        yield page
    finally:
        page.close()


def _latest(server: MiniWobServer, *, task: str = SUBDOMAIN, seed: int = SEED) -> tuple[int, dict[str, Any]]:
    return http_get_json(f"{server.base_url}/__r2g_reward/latest?task={task}&seed={seed}")


def test_d16a_url_seed_auto_starts_the_episode(started_page: Any, miniwob_server: MiniWobServer) -> None:
    """16(a)：``r2g_seed`` 触发自动开局，episode 预算被改写成 240s，无开局错误。"""
    assert started_page.evaluate("() => core.ept0 != null") is True
    assert started_page.evaluate("() => core.EPISODE_MAX_TIME") == EPISODE_MS
    assert started_page.evaluate("() => window.__R2G_START_ERROR || null") is None
    assert started_page.evaluate("() => window.WOB_EPISODE_ID") == 0  # review 必改 1.6：开局后仍为 0

    utterance = started_page.evaluate("() => core.getUtterance()")
    text = utterance if isinstance(utterance, str) else utterance.get("utterance")
    assert isinstance(text, str) and text.strip()
    assert goal_reader.sanitize_goal(text)

    # 没有 endEpisode → 没有 reward 记录
    status, payload = _latest(miniwob_server)
    assert status == 404 and payload == {"error": "no_reward"}


def test_d16a_pages_without_seed_do_not_auto_start(browser: Browser, miniwob_server: MiniWobServer) -> None:
    """16(a) 负例：无 ``r2g_seed`` 的页面不自动开局（不干扰人工调试）。"""
    page = browser.new_page()
    page.set_default_timeout(5000)
    try:
        page.goto(f"{miniwob_server.base_url}/miniwob/{SUBDOMAIN}.html", wait_until="load")
        page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=10000)
        assert page.evaluate("() => core.ept0 == null") is True
        assert page.evaluate("() => window.__R2G_START_ERROR || null") is None
    finally:
        page.close()


def test_d16b_same_seed_reproduces_the_same_goal(miniwob_server: MiniWobServer) -> None:
    """16(b)：同 URL 两次预读 utterance 相同（同 seed 确定性），且预读不产生 reward 记录。"""
    first = goal_reader.read_goal(SUBDOMAIN, SEED, port=miniwob_server.port, episode_ms=EPISODE_MS)
    second = goal_reader.read_goal(SUBDOMAIN, SEED, port=miniwob_server.port, episode_ms=EPISODE_MS)
    assert first == second
    assert first.strip() and '"' not in first

    status, payload = _latest(miniwob_server)
    assert status == 404 and payload == {"error": "no_reward"}

    other = goal_reader.read_goal(SUBDOMAIN, SEED + 1, port=miniwob_server.port, episode_ms=EPISODE_MS)
    assert other.strip()


def test_d16c_end_episode_posts_the_terminal_record(started_page: Any, miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """16(c)：页内 ``core.endEpisode(1)`` → 同步 POST → ``/latest`` 取到 ``raw>0, done=true``。"""
    started_page.evaluate("() => core.endEpisode(1)")

    deadline = time.monotonic() + 10
    status, record = 404, {"error": "no_reward"}
    while time.monotonic() < deadline:
        status, record = _latest(miniwob_server)
        if status == 200:
            break
        time.sleep(0.1)
    assert status == 200, record
    assert record["raw"] > 0
    assert record["done"] is True
    assert record["path"] == f"/miniwob/{SUBDOMAIN}.html"
    assert record["seed"] == str(SEED)
    assert record["reason"] == ""
    assert started_page.evaluate("() => window.WOB_EPISODE_ID") == 1

    lines = read_jsonl(reward_file)
    assert len(lines) == 1
    assert json.loads(json.dumps(lines[0]))["raw"] == record["raw"]


def test_d16c_page_timeout_posts_a_negative_reward(browser: Browser, miniwob_server: MiniWobServer) -> None:
    """16(c) 补充：页面自身超时 → ``raw=-1, done=true, reason='timed out'``（review 必改 1.5）。"""
    short_ms = 1500
    page = browser.new_page()
    page.set_default_timeout(20000)
    try:
        page.goto(goal_reader.page_url(SUBDOMAIN, port=miniwob_server.port, seed=SEED, episode_ms=short_ms), wait_until="load")
        page.wait_for_function(EPISODE_READY, timeout=20000)
        assert page.evaluate("() => core.EPISODE_MAX_TIME") == short_ms

        deadline = time.monotonic() + 15
        status, record = 404, {"error": "no_reward"}
        while time.monotonic() < deadline:
            status, record = _latest(miniwob_server)
            if status == 200:
                break
            time.sleep(0.2)
    finally:
        page.close()
    assert status == 200, record
    assert record["raw"] == -1
    assert record["reward"] == -1
    assert record["done"] is True
    assert record["reason"] == "timed out"


# ---------------------------------------------------------------------------------------------
# 16(d) H1 安全加固：HUD 不可见、START 不可点、奖励 POST 完好、goal 预读不变
# ---------------------------------------------------------------------------------------------


def test_d16d_hardening_hides_the_hud_from_the_agent_view_and_keeps_the_reward_post(started_page: Any, miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """16(d)（H1，审查报告 §2.V1）：HUD 全程零命中；成功终局照样 POST ``raw>0, done=true``。"""
    # 回合内：#query 指令区照旧可见（goal 预读依赖它），HUD 已隐藏
    mid_episode = started_page.evaluate("() => document.body.innerText")
    _assert_no_hud_leak(mid_episode)
    assert started_page.evaluate(HUD_DISPLAY) == "none"
    assert started_page.evaluate(COVER_DISPLAY) == "none"
    assert started_page.evaluate("() => document.getElementById('query').innerText").strip()
    assert mid_episode.strip()

    # 成功终局：奖励记录必须完好（加固绝不吞掉上报）
    started_page.evaluate("() => core.endEpisode(1)")
    deadline = time.monotonic() + 10
    status, record = 404, {"error": "no_reward"}
    while time.monotonic() < deadline:
        status, record = _latest(miniwob_server)
        if status == 200:
            break
        time.sleep(0.1)
    assert status == 200, record
    assert record["raw"] > 0 and record["done"] is True and record["reason"] == ""

    # 终局之后：HUD 不回写（updateDisplay 已置空）、覆盖层不重新出现、文本视角零命中
    time.sleep(0.3)
    _assert_no_hud_leak(started_page.evaluate("() => document.body.innerText"))
    assert started_page.evaluate(HUD_DISPLAY) == "none"
    assert started_page.evaluate(COVER_DISPLAY) == "none"
    assert started_page.evaluate("() => document.getElementById('reward-last').textContent") == "-"
    lines = read_jsonl(reward_file)
    assert len(lines) == 1 and lines[0]["raw"] == record["raw"]


def test_d16d_hardening_closes_the_start_reroll_path(started_page: Any) -> None:
    """16(d)（H1，审查报告 §2.V2）：失败终局后 START 覆盖层不出现、点不到、实例不重开。"""
    utterance_before = started_page.evaluate("() => core.getUtterance()")
    assert started_page.is_visible("#sync-task-cover") is False

    started_page.evaluate("() => core.endEpisode(0)")  # 错误提交的典型终局
    time.sleep(0.3)
    assert started_page.evaluate("() => window.WOB_DONE_GLOBAL") is True
    _assert_no_hud_leak(started_page.evaluate("() => document.body.innerText"))
    assert started_page.evaluate(COVER_DISPLAY) == "none"
    assert started_page.is_visible("#sync-task-cover") is False
    # 普通 DOM 点击（浏览器 agent 唯一的手段）无法触达覆盖层 → 点不动即无法重开
    with pytest.raises(PlaywrightTimeoutError):
        started_page.click("#sync-task-cover", timeout=1000)
    # 实例未被重开：同 seed 下重开会推进 RNG（utterance 改变），这里必须恒等
    assert started_page.evaluate("() => core.getUtterance()") == utterance_before
    assert started_page.evaluate("() => window.WOB_EPISODE_ID") == 1


# ---------------------------------------------------------------------------------------------
# r2 T5：终局信号 --terminal-cue（spec-r2 §4.3）+ T6：单次开局 --single-start（spec-r2 §4.2）
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def cue_server(html_root: Path, tmp_path: Path) -> Iterator[MiniWobServer]:
    """``--terminal-cue``伺服器（C2）：默认 off 的伺服器由模块级 ``miniwob_server`` fixture 覆盖。"""
    server = MiniWobServer(html_root, port=0, rewards_file=tmp_path / "rewards.jsonl", terminal_cue=True)
    server.start(background=True)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def single_server(html_root: Path, tmp_path: Path) -> Iterator[MiniWobServer]:
    """``--single-start``伺服器（C3）。"""
    server = MiniWobServer(html_root, port=0, rewards_file=tmp_path / "rewards.jsonl", terminal_cue=False, single_start=True)
    server.start(background=True)
    try:
        yield server
    finally:
        server.stop()


def _open_started(browser: Browser, server: MiniWobServer, *, seed: int = SEED) -> Any:
    page = browser.new_page()
    page.set_default_timeout(20000)
    page.goto(goal_reader.page_url(SUBDOMAIN, port=server.port, seed=seed, episode_ms=EPISODE_MS), wait_until="load")
    page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=20000)
    page.wait_for_function(EPISODE_READY, timeout=20000)
    return page


def _latest(server: MiniWobServer, *, task: str = SUBDOMAIN, seed: int = SEED) -> tuple[int, dict[str, Any]]:
    return http_get_json(f"{server.base_url}/__r2g_reward/latest?task={task}&seed={seed}")


def _wait_latest(server: MiniWobServer, *, seed: int = SEED) -> tuple[int, dict[str, Any]]:
    deadline = time.monotonic() + 10
    status, record = 404, {"error": "no_reward"}
    while time.monotonic() < deadline:
        status, record = _latest(server, seed=seed)
        if status == 200:
            break
        time.sleep(0.1)
    return status, record


CUE_TEXT = "() => document.getElementById('r2g-terminal-cue').innerText"
CUE_COUNT = "() => document.querySelectorAll('#r2g-terminal-cue').length"
CUE_DISPLAY = "() => getComputedStyle(document.getElementById('r2g-terminal-cue')).display"


def test_t5a_cue_is_neutral_and_constant_across_reward_values(browser: Browser, cue_server: MiniWobServer) -> None:
    """T5(a)：endEpisode(1) 与 endEpisode(-1) 后 cue 文本恒为且仅为 ``EPISODE ENDED``（两次一致）。"""
    page = _open_started(browser, cue_server)
    try:
        assert page.evaluate("() => document.getElementById('r2g-terminal-cue')") is None  # 开局时无 cue
        for value in (1, -1, 1):
            page.evaluate(f"() => core.endEpisode({value})")
            time.sleep(0.2)
            assert page.evaluate(CUE_TEXT) == "EPISODE ENDED"
    finally:
        page.close()


def test_t5b_cue_is_visible_but_leaks_nothing(browser: Browser, cue_server: MiniWobServer) -> None:
    """T5(b)：cue 可见（非 display:none、出现在 innerText）；HUD/START 加固零命中回归。"""
    page = _open_started(browser, cue_server)
    try:
        page.evaluate("() => core.endEpisode(1)")
        time.sleep(0.3)
        assert page.evaluate(CUE_DISPLAY) != "none"
        body = page.evaluate("() => document.body.innerText")
        assert "EPISODE ENDED" in body
        _assert_no_hud_leak(body)  # Last reward / Time left / Episodes done / START 零命中
        assert "raw" not in page.evaluate(CUE_TEXT).lower()
        assert page.evaluate("() => document.getElementById('query').innerText").strip()  # #query 不被遮内容
    finally:
        page.close()


def test_t5c_cue_does_not_touch_the_reward_post(browser: Browser, cue_server: MiniWobServer, tmp_path: Path) -> None:
    """T5(c)：cue 不改判分 —— /latest 记录与不加 cue 的伺服器逐字段一致（POST 完好、单实例单文本）。"""
    page = _open_started(browser, cue_server)
    try:
        page.evaluate("() => core.endEpisode(1)")
        status, record = _wait_latest(cue_server)
        assert status == 200, record
        assert record["raw"] > 0 and record["done"] is True and record["reason"] == ""
        assert record["path"] == f"/miniwob/{SUBDOMAIN}.html" and record["seed"] == str(SEED)
        # (d) 幂等：重复 endEpisode 仍单实例单文本，且 rewards.jsonl 每次 endEpisode 恰一行
        page.evaluate("() => core.endEpisode(0)")
        time.sleep(0.3)
        assert page.evaluate(CUE_COUNT) == 1
        assert page.evaluate(CUE_TEXT) == "EPISODE ENDED"
        lines = read_jsonl(tmp_path / "rewards.jsonl")
        assert len(lines) == 2  # endEpisode(1) + endEpisode(0)，每次一行（cue 不额外 POST）
    finally:
        page.close()


def test_t6a_second_load_in_the_same_tab_does_not_restart(browser: Browser, single_server: MiniWobServer) -> None:
    """T6(a)：同 tab 二次 goto → 不自动开局（ept0 不更新）、不产生新 reward 记录。"""
    page = _open_started(browser, single_server)
    try:
        page.evaluate("() => core.endEpisode(1)")  # 首局终局 → 1 条记录
        status, record = _wait_latest(single_server)
        assert status == 200 and record["raw"] > 0

        page.goto(goal_reader.page_url(SUBDOMAIN, port=single_server.port, seed=SEED, episode_ms=EPISODE_MS), wait_until="load")
        page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=20000)
        time.sleep(1.0)  # 给任何非法自动开局留出暴露时间
        assert page.evaluate("() => core.ept0 == null") is True  # 未开局
        assert page.evaluate("() => window.WOB_EPISODE_ID") == 0
        assert read_jsonl(single_server.collector.rewards_file) is not None
        assert len(read_jsonl(single_server.collector.rewards_file)) == 1  # 无新记录
    finally:
        page.close()


def test_t6e_intercepted_page_hardening_blocks_the_start_overlay(browser: Browser, single_server: MiniWobServer) -> None:
    """T6(e)（review-r2 M2）：拦截态点击 START 覆盖层 → 无新记录、保持未开局；文本视角零命中。"""
    page = _open_started(browser, single_server)
    try:
        page.evaluate("() => core.endEpisode(1)")
        _wait_latest(single_server)
        page.goto(goal_reader.page_url(SUBDOMAIN, port=single_server.port, seed=SEED, episode_ms=EPISODE_MS), wait_until="load")
        page.wait_for_function("() => window.WOB_TASK_READY === true", timeout=20000)
        time.sleep(0.5)

        assert page.evaluate("() => core.ept0 == null") is True
        assert page.evaluate("() => core.startEpisodeReal.toString()") == "function () {}"  # stub 在补丁求值期生效
        assert page.evaluate("() => core.startEpisode.toString()") == "function () {}"
        # 拦截态同样 V2 加固：覆盖层与 HUD 隐藏，普通 DOM 点击无法重开
        assert page.evaluate(COVER_DISPLAY) in ("none", "missing")
        with pytest.raises(PlaywrightTimeoutError):
            page.click("#sync-task-cover", timeout=800)
        time.sleep(0.5)
        assert page.evaluate("() => core.ept0 == null") is True
        assert len(read_jsonl(single_server.collector.rewards_file)) == 1  # 无任何新 reward 记录
        lowered = page.evaluate("() => document.body.innerText").lower()
        hits = [k for k in ("start", "last reward", "time left", "episodes done") if k in lowered]
        assert not hits, hits
    finally:
        page.close()


def test_t6b_goal_preread_is_unaffected_by_single_start(single_server: MiniWobServer) -> None:
    """T6(b)：预读路径（独立 context，sessionStorage 不跨 context）不受影响 —— utterance 正常读取。

    注意：本用例不得持有 ``browser`` fixture（read_goal 需要本线程独立的 sync context）。
    """
    first = goal_reader.read_goal(SUBDOMAIN, SEED, port=single_server.port, episode_ms=EPISODE_MS)
    second = goal_reader.read_goal(SUBDOMAIN, SEED, port=single_server.port, episode_ms=EPISODE_MS)
    assert first.strip() and first == second  # 同 seed 确定性
    assert _latest(single_server)[0] == 404  # 预读不产生 reward 记录


def test_t6c_default_server_still_restarts_on_second_load(browser: Browser, miniwob_server: MiniWobServer) -> None:
    """T6(c)：flag off（默认伺服器）时二次 load 行为与 r1 一致 —— 自动重开局。"""
    page = browser.new_page()
    page.set_default_timeout(20000)
    url = goal_reader.page_url(SUBDOMAIN, port=miniwob_server.port, seed=SEED, episode_ms=EPISODE_MS)
    try:
        page.goto(url, wait_until="load")
        page.wait_for_function(EPISODE_READY, timeout=20000)
        page.goto(url, wait_until="load")  # 同 tab 二次加载
        page.wait_for_function(EPISODE_READY, timeout=20000)  # r1：自动重开局 → ept0 再次非空
        assert page.evaluate("() => core.ept0 != null") is True
        assert page.evaluate("() => window.__R2G_START_ERROR || null") is None
    finally:
        page.close()
