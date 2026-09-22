"""B 组：伺服与补丁（spec §9 B6-B9）。

B6 原样字节 + no-store；B7 ``core.js`` = 原文 + 纯追加补丁；B8 404/穿越防护/无目录列表；B9 端口占用硬失败。
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from record2gherkin.benchmark.miniwob_server import (
    AUTO_START_PATCH,
    AUTO_START_PATCH_SINGLE,
    REWARD_HOOK_PATCH,
    MiniWobServer,
    PortInUseError,
    patch_core_js,
)
from tests.record2gherkin.benchmark.conftest import http_get

#: 补丁里必须逐字出现的结构行（spec §3.2 修正版；改动这些行会静默改变开局语义）。
VERBATIM_PATCH_LINES = (
    'if (window.WOB_TASK_READY === true && document.readyState === "complete" && core.cover_div) {',
    "Math.seedrandom(seed);",
    "core.EPISODE_MAX_TIME = ms;",
    "core.startEpisodeReal();",
    "clearInterval(timer); /* 只在开局成功后停止轮询 */",
    'window.__R2G_START_ERROR = "WOB_TASK_READY timeout";',
    'x.open("POST", "/__r2g_reward", false); /* 同步：确保页面销毁前送达 */',
    "var orig = core.endEpisode;",
)

#: H1 安全加固（审查报告 §2.V1/V2）：必须在补丁 A 的标记**之内**、``startEpisodeReal()`` 之后，
#: 且用 ``display:none`` 而不是 remove（endEpisode 仍要写 ``#episode-id`` 等子元素）。
HARDENING_PATCH_LINES = (
    'var hud = document.getElementById("reward-display");',
    'if (hud) { hud.style.display = "none"; }',
    "core.updateDisplay = function () {}; /* 终局不回写 HUD 文本 */",
    "core.startEpisode = function () {}; /* endEpisode 尾部不再重新显示 START 覆盖层 */",
)


def test_b6_vendored_bytes_are_served_unchanged(miniwob_server: MiniWobServer, html_root: Path) -> None:
    """B6：``GET /miniwob/click-test.html`` → 200 且与 vendored 文件字节一致；``Cache-Control: no-store``。"""
    status, body, headers = http_get(f"{miniwob_server.base_url}/miniwob/click-test.html")
    assert status == 200
    assert body == (html_root / "miniwob" / "click-test.html").read_bytes()
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Type"].startswith("text/html")


def test_b7_core_js_is_the_vendored_file_plus_appended_patches(miniwob_server: MiniWobServer, html_root: Path) -> None:
    """B7：前缀逐字节一致；两段补丁标记与关键字面量齐全；非 ``core.js`` 文件不加补丁。"""
    source = (html_root / "core" / "core.js").read_bytes()
    status, body, headers = http_get(f"{miniwob_server.base_url}/core/core.js")
    assert status == 200
    assert body.startswith(source)
    assert headers["Content-Type"] == "text/javascript"
    assert headers["Cache-Control"] == "no-store"
    assert body == patch_core_js(source)
    assert body == patch_core_js(source)  # 确定性：同一文件两次请求字节相同

    text = body.decode("utf-8")
    for marker in ("__R2G_PATCH_START__", "__R2G_PATCH_END__", "__R2G_REWARD_HOOK__", "startEpisodeReal", "/__r2g_reward"):
        assert marker in text
    for line in VERBATIM_PATCH_LINES:
        assert line in text, f"patch line missing: {line}"
    appended = body[len(source) :].decode("utf-8").strip()
    assert appended.startswith("/* __R2G_PATCH_START__ */")
    assert appended.endswith("})();")
    assert AUTO_START_PATCH in appended and REWARD_HOOK_PATCH in appended

    # 补丁只追加到 core.js：任务页与其它 js 都是原样字节
    _, page_body, _ = http_get(f"{miniwob_server.base_url}/miniwob/click-test.html")
    assert b"__R2G" not in page_body
    _, utils_body, _ = http_get(f"{miniwob_server.base_url}/common/ui_utils.js")
    assert utils_body == (html_root / "common" / "ui_utils.js").read_bytes()


def test_b7_core_js_with_query_is_patched_too(miniwob_server: MiniWobServer, html_root: Path) -> None:
    """B7 补充：带 query 的 ``/core/core.js?x=1`` 同样走补丁分支（浏览器缓存击穿场景）。"""
    source = (html_root / "core" / "core.js").read_bytes()
    status, body, _ = http_get(f"{miniwob_server.base_url}/core/core.js?v=7")
    assert status == 200
    assert body == patch_core_js(source)


def test_b7_hardening_lives_inside_patch_a_after_the_auto_start(miniwob_server: MiniWobServer) -> None:
    """B7 补充（H1）：安全加固四行位于补丁 A 标记内、``startEpisodeReal()`` 之后，且不触碰补丁 B。"""
    _, body, _ = http_get(f"{miniwob_server.base_url}/core/core.js")
    text = body.decode("utf-8")

    start = text.index("/* __R2G_PATCH_START__ */")
    end = text.index("/* __R2G_PATCH_END__ */")
    assert start < end
    patch_a = text[start : end + len("/* __R2G_PATCH_END__ */")]
    for line in HARDENING_PATCH_LINES:
        assert line in patch_a, f"hardening line missing from patch A: {line}"
    # 加固必须在开局成功之后（开局失败时页面上不存在 HUD/cover，也不应改写 core 方法）
    assert patch_a.index("core.startEpisodeReal();") < patch_a.index("core.updateDisplay = function () {};")
    assert patch_a.index("core.updateDisplay = function () {};") < patch_a.index("core.startEpisode = function () {};")
    assert "remove(" not in patch_a  # 绝不移除节点：endEpisode 仍要写 #episode-id

    # 补丁 B（reward hook）逐字未变
    assert "var orig = core.endEpisode;" in text
    assert 'x.open("POST", "/__r2g_reward", false); /* 同步：确保页面销毁前送达 */' in text


@pytest.mark.parametrize(
    "path",
    [
        "/missing.html",
        "/miniwob/does-not-exist.html",
        "/",
        "/miniwob/",
        "/core/",
        "/../etc/passwd",
        "/miniwob/../../etc/passwd",
        "/%2e%2e/etc/passwd",
        "/miniwob/%2e%2e/%2e%2e/etc/passwd",
        "/__r2g_reward",
    ],
)
def test_b8_missing_paths_and_traversal_are_404(miniwob_server: MiniWobServer, path: str) -> None:
    """B8：不存在路径、目录（无列表）、穿越形态一律 404。"""
    status, body, headers = http_get(f"{miniwob_server.base_url}{path}")
    assert status == 404, f"{path} -> {status}"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body.decode("utf-8"))["error"] == "not_found"
    assert b"root:" not in body  # 真正的 /etc/passwd 内容绝不出现在响应里


def test_b9_busy_port_is_a_hard_error(html_root: Path, reward_file: Path) -> None:
    """B9：端口被占 → 启动硬失败（绝不自动换端口，URL 是实验参数）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = int(blocker.getsockname()[1])
        server = MiniWobServer(html_root, port=port, rewards_file=reward_file)
        with pytest.raises(PortInUseError):
            server.start()
        assert server.port == port


def test_healthz_reports_root_patch_flag_and_reward_count(miniwob_server: MiniWobServer, html_root: Path) -> None:
    """B 组补充：``/healthz`` 契约（spec §3.3）。"""
    from tests.record2gherkin.benchmark.conftest import http_get_json, http_post_json

    status, health = http_get_json(f"{miniwob_server.base_url}/healthz")
    assert status == 200
    assert health == {"served_root": str(html_root.resolve()), "patched": True, "rewards": 0}

    http_post_json(
        f"{miniwob_server.base_url}/__r2g_reward",
        {"path": "/miniwob/click-test.html", "seed": "5", "reward": 1, "raw": 1, "done": True, "reason": "", "ts": "2026-01-01T00:00:00Z"},
    )
    _, health = http_get_json(f"{miniwob_server.base_url}/healthz")
    assert health["rewards"] == 1


# ---------------------------------------------------------------------------------------------
# r2 T5(e)/T6(d)：补丁层离线字节断言（spec-r2 §4.1-§4.3）
# ---------------------------------------------------------------------------------------------


def test_t5e_off_flags_reproduce_the_r1_bytes(html_root: Path) -> None:
    """T5(e)/T6(d) 前置：两个 r2 flag 全 off → ``patch_core_js`` 输出与 r1 逐字节一致、无新标记。"""
    source = (html_root / "core" / "core.js").read_bytes()
    r1_bytes = source + f"\n{AUTO_START_PATCH}\n{REWARD_HOOK_PATCH}\n".encode("utf-8")
    for kwargs in ({}, {"terminal_cue": False, "single_start": False}):
        assert patch_core_js(source, **kwargs) == r1_bytes
    text = patch_core_js(source).decode("utf-8")
    assert "__R2G_PATCH_SINGLE_START__" not in text
    assert "__R2G_TERMINAL_CUE__" not in text
    assert "sessionStorage" not in text
    assert "EPISODE ENDED" not in text


def test_t6d_single_variant_differs_from_patch_a_in_exactly_three_places() -> None:
    """T6(d)：single 变体相对现状补丁 A 恰三处不同（标记行 / 新增① / 新增②），其余逐字节一致。"""
    import difflib

    a_lines = AUTO_START_PATCH.splitlines(keepends=True)
    s_lines = AUTO_START_PATCH_SINGLE.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, a_lines, s_lines, autojunk=False)
    ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    kinds = [op[0] for op in ops]
    assert kinds == ["replace", "insert", "insert"], kinds

    tag, a1, a2, s1, s2 = ops[0]
    assert tag == "replace" and a2 - a1 == 1 and s2 - s1 == 1  # 标记行
    assert "__R2G_PATCH_START__" in "".join(a_lines[a1:a2]) and "__R2G_PATCH_SINGLE_START__" in "".join(s_lines[s1:s2])

    tag, a1, a2, s1, s2 = ops[1]
    assert tag == "insert" and a1 == a2  # 新增①：拦截分支（M2 加固）
    guard = "".join(s_lines[s1:s2])
    assert 'sessionStorage.getItem("r2g_started") !== null' in guard
    assert "core.startEpisodeReal = function () {};" in guard
    assert "core.startEpisode = function () {};" in guard
    assert "core.updateDisplay = function () {};" in guard
    assert '"#reward-display, #sync-task-cover { display: none !important; }"' in guard
    assert "return;" in guard

    tag, a1, a2, s1, s2 = ops[2]
    assert tag == "insert" and a1 == a2  # 新增②：开局成功登记
    setitem = "".join(s_lines[s1:s2])
    assert 'sessionStorage.setItem("r2g_started", seed);' in setitem

    # 开局成功分支的安全加固四行逐字保留（H1），且成功分支仍在新增② 之前
    for line in HARDENING_PATCH_LINES:
        assert line in AUTO_START_PATCH_SINGLE
    single = AUTO_START_PATCH_SINGLE
    assert single.index("core.startEpisodeReal();") < single.index('sessionStorage.setItem("r2g_started", seed);')
    assert single.index("core.updateDisplay = function () {}; /* 终局不回写 HUD 文本 */") < single.index("clearInterval(timer);")


def test_t6d_patch_composition_order_and_markers(html_root: Path) -> None:
    """T6(d)/T5(e)：补丁顺序 = 原文 → 补丁A(single) → REWARD_HOOK → TERMINAL_CUE；标记齐全。"""
    source = (html_root / "core" / "core.js").read_bytes()
    text = patch_core_js(source, terminal_cue=True, single_start=True).decode("utf-8")
    assert text.startswith(source.decode("utf-8"))
    i_single = text.index("/* __R2G_PATCH_SINGLE_START__ */")
    i_hook = text.index("/* __R2G_REWARD_HOOK__ */")
    i_cue = text.index("/* __R2G_TERMINAL_CUE__ */")
    assert i_single < i_hook < i_cue

    from record2gherkin.benchmark.miniwob_server import TERMINAL_CUE_PATCH

    assert AUTO_START_PATCH_SINGLE in text and REWARD_HOOK_PATCH in text and TERMINAL_CUE_PATCH in text
    # 两个 wrapper 都包 core.endEpisode：REWARD_HOOK 在 TERMINAL_CUE 之前 —— chain 顺序与叠加断言一致
    assert text.count("var orig = core.endEpisode;") == 2
    # 单独 flag 组合的标记
    assert "__R2G_TERMINAL_CUE__" not in patch_core_js(source, single_start=True).decode("utf-8")
    assert "__R2G_PATCH_SINGLE_START__" not in patch_core_js(source, terminal_cue=True).decode("utf-8")
