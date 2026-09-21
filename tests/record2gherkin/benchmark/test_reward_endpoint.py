"""C 组：reward 收集端点（spec §9 C10-C13，契约见 §3.3）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from record2gherkin.benchmark.miniwob_server import MiniWobServer
from tests.record2gherkin.benchmark.conftest import (
    http_get_json,
    http_post_json,
    http_post_raw,
    read_jsonl,
)

REWARD_PATH = "/__r2g_reward"


def _payload(path: str = "/miniwob/click-test.html", seed: object = "11", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"path": path, "seed": seed, "reward": 1, "raw": 1, "done": True, "reason": "", "ts": "2026-01-01T00:00:00Z"}
    payload.update(overrides)
    return payload


def test_c10_valid_post_lands_in_memory_and_jsonl(miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """C10：合法 POST → 200；``/latest`` 取回一致记录（含 ``received_at``）。"""
    status, body = http_post_json(f"{miniwob_server.base_url}{REWARD_PATH}", _payload(seed="11"))
    assert status == 200
    assert body["ok"] is True

    status, record = http_get_json(f"{miniwob_server.base_url}/__r2g_reward/latest?task=click-test&seed=11")
    assert status == 200
    assert record["path"] == "/miniwob/click-test.html"
    assert record["seed"] == "11"
    assert record["raw"] == 1
    assert record["done"] is True
    assert record["reason"] == ""
    assert record["received_at"]

    lines = read_jsonl(reward_file)
    assert len(lines) == 1
    assert lines[0]["raw"] == 1
    assert lines[0]["received_at"] == record["received_at"]


def test_c11_last_wins_and_seeds_do_not_leak(miniwob_server: MiniWobServer) -> None:
    """C11：同 (task, seed) 两次 POST → last-wins；不同 seed 互不串。"""
    base = miniwob_server.base_url
    http_post_json(f"{base}{REWARD_PATH}", _payload(seed="11", raw=0, reward=0, done=True, reason="timed out", ts="t1"))
    http_post_json(f"{base}{REWARD_PATH}", _payload(seed="11", raw=1, reward=1, done=True, reason="", ts="t2"))
    http_post_json(f"{base}{REWARD_PATH}", _payload(seed="12", raw=0.5, reward=0.5, done=False, reason="", ts="t3"))

    _, eleven = http_get_json(f"{base}/__r2g_reward/latest?task=click-test&seed=11")
    _, twelve = http_get_json(f"{base}/__r2g_reward/latest?task=click-test&seed=12")
    assert eleven["raw"] == 1 and eleven["ts"] == "t2"
    assert twelve["raw"] == 0.5 and twelve["ts"] == "t3"

    # 另一个任务页不受影响
    _, other = http_get_json(f"{base}/__r2g_reward/latest?task=click-button&seed=11")
    assert other == {"error": "no_reward"}


def test_c11_latest_needs_both_task_and_seed(miniwob_server: MiniWobServer) -> None:
    """C11 补充：``/latest`` 缺参 → 404 no_reward；``task`` 只按 path 末段匹配。"""
    base = miniwob_server.base_url
    http_post_json(f"{base}{REWARD_PATH}", _payload(path="/miniwob/email-inbox-forward-nl.html", seed="7"))
    assert http_get_json(f"{base}/__r2g_reward/latest?task=email-inbox-forward-nl&seed=7")[0] == 200
    assert http_get_json(f"{base}/__r2g_reward/latest?task=email-inbox-forward-nl")[0] == 404
    assert http_get_json(f"{base}/__r2g_reward/latest?seed=7")[0] == 404
    assert http_get_json(f"{base}/__r2g_reward/latest")[0] == 404
    assert http_get_json(f"{base}/__r2g_reward/latest?task=email-inbox-forward-nl&seed=8")[0] == 404


def test_c12_every_post_appends_exactly_one_line(miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """C12：每次 POST 追加恰一行（文件与内存一致）。"""
    base = miniwob_server.base_url
    for index in range(4):
        http_post_json(f"{base}{REWARD_PATH}", _payload(seed=str(index), raw=index))
    lines = read_jsonl(reward_file)
    assert len(lines) == 4
    assert [line["seed"] for line in lines] == ["0", "1", "2", "3"]
    _, health = http_get_json(f"{base}/healthz")
    assert health["rewards"] == 4


@pytest.mark.parametrize(
    "body,purpose",
    [
        ("not json at all", "invalid json"),
        ('{"nope": "5", "path": "/miniwob/click-test.html"}', "missing seed"),
        ('{"seed": "5", "nope": "/miniwob/click-test.html"}', "missing path"),
        ('{"path": "", "seed": "5"}', "empty path"),
        ('{"path": "/miniwob/click-test.html", "seed": ""}', "empty seed"),
        ('{"path": "/miniwob/click-test.html", "seed": null}', "null seed"),
        ('["not", "an", "object"]', "non-object body"),
    ],
)
def test_c13_invalid_posts_are_400_and_write_nothing(miniwob_server: MiniWobServer, reward_file: Path, body: str, purpose: str) -> None:
    """C13：非法 JSON / 缺 seed / 缺 path → 400 且不落盘、不入内存。"""
    status, payload = http_post_raw(f"{miniwob_server.base_url}{REWARD_PATH}", body.encode("utf-8"))
    assert status == 400, purpose
    assert "error" in payload
    assert not reward_file.is_file()
    _, health = http_get_json(f"{miniwob_server.base_url}/healthz")
    assert health["rewards"] == 0
    assert http_get_json(f"{miniwob_server.base_url}/__r2g_reward/latest?task=click-test&seed=5")[0] == 404


def test_c13_reward_post_to_another_route_is_404(miniwob_server: MiniWobServer, reward_file: Path) -> None:
    """C13 补充：POST 到未声明的路径 → 404，不落盘。"""
    status, _ = http_post_json(f"{miniwob_server.base_url}/__r2g_reward/other", _payload())
    assert status == 404
    assert not reward_file.is_file()


def test_reward_may_be_negative_on_timeout(miniwob_server: MiniWobServer) -> None:
    """C 组补充：页面自身超时的记录（``raw=-1, done=true, reason="timed out"``）原样落盘。"""
    base = miniwob_server.base_url
    http_post_json(f"{base}{REWARD_PATH}", _payload(seed="3", reward=-1, raw=-1, done=True, reason="timed out"))
    _, record = http_get_json(f"{base}/__r2g_reward/latest?task=click-test&seed=3")
    assert record["raw"] == -1
    assert record["done"] is True
    assert record["reason"] == "timed out"
