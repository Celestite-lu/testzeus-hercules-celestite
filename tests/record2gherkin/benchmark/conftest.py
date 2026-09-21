"""Shared fixtures/helpers for the MiniWoB++ benchmark test-suite (spec §9).

Everything here is offline: pure-string rendering, an in-thread patch server on the loopback
interface, hand-written result rows and monkeypatched sub-process seams.  No LLM, no external
network, no API key.  The browser group (``test_browser_miniwob.py``) additionally needs a local
chromium and skips itself when there is none.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
from record2gherkin.benchmark import tasks as tasks_module
from record2gherkin.benchmark.miniwob_server import MiniWobServer

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "record2gherkin" / "benchmark"
MINIWOB_HTML_ROOT = BENCHMARK_DIR / "miniwob_html"
TASKS_PATH = BENCHMARK_DIR / "tasks.json"
PROVENANCE_PATH = BENCHMARK_DIR / "PROVENANCE.md"


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite offline: Hercules' config initialises Sentry at import time (spec §4.3 of evaluation)."""
    os.environ.setdefault("ENABLE_TELEMETRY", "0")


# ---------------------------------------------------------------------------------------------
# HTTP helpers (loopback only, proxy disabled — exp001 已知问题 11)
# ---------------------------------------------------------------------------------------------

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str) -> tuple[int, bytes, Mapping[str, str]]:
    """GET returning ``(status, body, headers)``; 4xx responses are returned instead of raised."""
    try:
        with _NO_PROXY_OPENER.open(url, timeout=10) as response:  # noqa: S310 - loopback
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def http_get_json(url: str) -> tuple[int, dict[str, Any]]:
    status, body, _ = http_get(url)
    return status, json.loads(body.decode("utf-8"))


def http_post_json(url: str, payload: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return http_post_raw(url, body)


def http_post_raw(url: str, body: bytes, content_type: str = "application/json") -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with _NO_PROXY_OPENER.open(request, timeout=10) as response:  # noqa: S310 - loopback
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def benchmark_dir() -> Path:
    return BENCHMARK_DIR


@pytest.fixture(scope="session")
def html_root() -> Path:
    """The vendored tree; the whole suite is skipped when the build step has not run yet."""
    if not MINIWOB_HTML_ROOT.is_dir():
        pytest.skip(f"vendored html tree missing at {MINIWOB_HTML_ROOT}; run the vendor_miniwob.py build step")
    return MINIWOB_HTML_ROOT


@pytest.fixture(scope="session")
def tasks(html_root: Path) -> list[dict[str, Any]]:
    return tasks_module.load_tasks()


@pytest.fixture()
def reward_file(tmp_path: Path) -> Path:
    return tmp_path / "rewards.jsonl"


@pytest.fixture()
def miniwob_server(html_root: Path, reward_file: Path) -> Iterator[MiniWobServer]:
    """In-thread patch server on an ephemeral loopback port with a per-test rewards file."""
    server = MiniWobServer(html_root, port=0, rewards_file=reward_file)
    server.start(background=True)
    try:
        yield server
    finally:
        server.stop()
