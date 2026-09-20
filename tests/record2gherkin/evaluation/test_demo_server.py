"""B 组：demo 服务（spec §9 B13-B15，线程内起服务 + urllib 回环）。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from record2gherkin.evaluation.demo_app import STATUS_ALL_TEXT
from record2gherkin.evaluation.demo_server import (
    CONTROL_PATH,
    HEALTH_PATH,
    DemoServer,
    PortInUseError,
)
from tests.record2gherkin.evaluation.conftest import (
    http_get_json,
    http_get_text,
    http_post_json,
    http_post_raw,
)


def test_b13_healthz_defaults_to_m0(tmp_path: Any) -> None:
    """B13 ``/healthz`` 返回当前 (mutation, seed)，默认 M0/0。"""
    with DemoServer(port=0) as server:
        status, payload = http_get_json(f"{server.base_url}{HEALTH_PATH}")
        assert status == 200
        assert payload == {"mutation": "M0", "seed": 0}


def test_b14_control_switches_document() -> None:
    """B14 ``POST /__control`` 合法体后 ``/`` 内容随 M0→M3 变化（同 URL 不同 HTML）。"""
    with DemoServer(port=0) as server:
        _, before = http_get_text(f"{server.base_url}/")
        status, payload = http_post_json(f"{server.base_url}{CONTROL_PATH}", {"mutation": "M3", "seed": 42})
        assert status == 200 and payload == {"mutation": "M3", "seed": 42}
        _, after = http_get_text(f"{server.base_url}/")
        assert before != after
        assert 'id="search-input"' in before and 'id="search-input"' not in after
        assert "m3-" in after
        # 同一 (mutation, seed) 下字节级一致 + no-store
        assert http_get_text(f"{server.base_url}/")[1] == after
        status, health = http_get_json(f"{server.base_url}{HEALTH_PATH}")
        assert health == {"mutation": "M3", "seed": 42}


def test_b14b_any_path_serves_the_same_document() -> None:
    """B14 补充：任意非保留路径返回同一文档（hash 路由可达 ``#/order``）。"""
    with DemoServer(port=0) as server:
        _, root = http_get_text(f"{server.base_url}/")
        _, deep = http_get_text(f"{server.base_url}/#/order")
        assert root == deep
        assert 'id="order-view"' in root
        assert "共 3 件商品" in root


def test_b15_invalid_control_body_is_rejected() -> None:
    """B15 非法 mutation → 400 且状态不变。"""
    with DemoServer(port=0) as server:
        http_post_json(f"{server.base_url}{CONTROL_PATH}", {"mutation": "M3", "seed": 7})
        status, payload = http_post_json(f"{server.base_url}{CONTROL_PATH}", {"mutation": "M9", "seed": 0})
        assert status == 400
        assert "invalid mutation" in payload["error"]
        status, payload = http_post_json(f"{server.base_url}{CONTROL_PATH}", {"mutation": "M1", "seed": -1})
        assert status == 400
        status, payload = http_post_json(f"{server.base_url}{CONTROL_PATH}", {"mutation": "M1", "seed": "abc"})
        assert status == 400
        assert http_get_json(f"{server.base_url}{HEALTH_PATH}")[1] == {"mutation": "M3", "seed": 7}


def test_b15b_malformed_body_and_unknown_path() -> None:
    """B15 补充：畸形 JSON → 400；未知 POST 路径 → 404；状态不变。"""
    with DemoServer(port=0) as server:
        status, body = http_post_raw(f"{server.base_url}{CONTROL_PATH}", b"{not json")
        assert status == 400
        assert "invalid json" in body
        status, _ = http_post_json(f"{server.base_url}/nope", {"mutation": "M3", "seed": 1})
        assert status == 404
        assert http_get_json(f"{server.base_url}{HEALTH_PATH}")[1] == {"mutation": "M0", "seed": 0}


def test_port_in_use_is_a_hard_error() -> None:
    """补充（spec §3.1）：端口被占时报错退出，不自动换端口。"""
    with DemoServer(port=0) as server:
        other = DemoServer(port=server.port)
        with pytest.raises(PortInUseError):
            other.start()


def test_server_state_renders_current_mutation() -> None:
    """补充：``DemoState`` 校验与渲染同源。"""
    from record2gherkin.evaluation.demo_server import DemoState

    state = DemoState()
    assert state.as_dict() == {"mutation": "M0", "seed": 0}
    assert STATUS_ALL_TEXT in state.render()
    state.set("M3", 5)
    assert state.as_dict() == {"mutation": "M3", "seed": 5}
    assert "m3-" in state.render()
    with pytest.raises(ValueError):
        state.set("M9", 0)
    with pytest.raises(ValueError):
        state.set("M0", True)
    assert json.loads(json.dumps(state.as_dict())) == {"mutation": "M3", "seed": 5}
