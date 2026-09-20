"""Shared fixtures/helpers for the evaluation test-suite (spec §9).

Everything here is offline: pure-string rendering assertions, an in-thread demo server on the
loopback interface, hand-written JUnit XML, and sub-process seams monkeypatched away.  No LLM, no
external network, no API key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import pytest
from record2gherkin.evaluation.demo_server import DemoServer

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALUATION_DIR = REPO_ROOT / "record2gherkin" / "evaluation"
EXPERIMENTS_DIR = REPO_ROOT / "dev_runs" / "experiments"
RECORDINGS_DIR = EXPERIMENTS_DIR / "recordings"
FEATURES_DIR = EXPERIMENTS_DIR / "features"

FAKE_API_KEY = "sk-test-0123456789abcdef0123456789abcdef"


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite offline: Hercules' config initialises Sentry at import time (spec §4.3).

    ``ENABLE_TELEMETRY`` is read at import time, hence it must be set before any module imports
    ``testzeus_hercules``.
    """
    os.environ.setdefault("ENABLE_TELEMETRY", "0")


# ---------------------------------------------------------------------------------------------
# HTTP helpers (loopback only)
# ---------------------------------------------------------------------------------------------


#: 回环请求禁用代理：macOS 系统代理会把 127.0.0.1 的请求转给代理并返回 502。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get_text(url: str) -> tuple[int, str]:
    with _NO_PROXY_OPENER.open(url, timeout=5) as response:  # noqa: S310 - loopback
        return response.status, response.read().decode("utf-8")


def http_get_json(url: str) -> tuple[int, dict[str, Any]]:
    status, body = http_get_text(url)
    return status, json.loads(body)


def http_post_json(url: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with _NO_PROXY_OPENER.open(request, timeout=5) as response:  # noqa: S310 - loopback
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def http_post_raw(url: str, body: bytes, content_type: str = "application/json") -> tuple[int, str]:
    """POST raw bytes; 4xx responses are returned instead of raised (no-proxy opener)."""
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with _NO_PROXY_OPENER.open(request, timeout=5) as response:  # noqa: S310 - loopback
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


# ---------------------------------------------------------------------------------------------
# JUnit XML builders (spec §9 C 组)
# ---------------------------------------------------------------------------------------------


def build_junit_xml(
    testcases: Sequence[Mapping[str, Any]],
    *,
    suite_properties: Mapping[str, str] | None = None,
    suite_name: str = "MiniShop 商城",
    suite_time: str | None = "12.5",
) -> str:
    """Hand-written Hercules-shaped JUnit XML (junitparser layout, ``junit_helper.py``)."""
    attrs = [f'name="{suite_name}"', f'tests="{len(testcases)}"']
    if suite_time is not None:
        attrs.append(f'time="{suite_time}"')
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<testsuites>", f"<testsuite {' '.join(attrs)}>"]
    if suite_properties:
        lines.append("<properties>")
        for name, value in suite_properties.items():
            lines.append(f'<property name="{name}" value="{value}"/>')
        lines.append("</properties>")
    for case in testcases:
        case_attrs = [f'name="{case.get("name", "scenario")}"', f'classname="{case.get("classname", "feature")}"']
        if case.get("time") is not None:
            case_attrs.append(f'time="{case["time"]}"')
        lines.append(f"<testcase {' '.join(case_attrs)}>")
        properties = case.get("properties") or {}
        if properties:
            lines.append("<properties>")
            for name, value in properties.items():
                lines.append(f'<property name="{name}" value="{value}"/>')
            lines.append("</properties>")
        failure = case.get("failure")
        if failure:
            message = failure.get("message", "assertion failed")
            lines.append(f'<failure message="{message}">{failure.get("text", "")}</failure>')
        for entry in case.get("system_out") or []:
            lines.append(f"<system-out>{entry}</system-out>")
        lines.append("</testcase>")
    lines.extend(["</testsuite>", "</testsuites>"])
    return "\n".join(lines) + "\n"


def write_junit(tmp_path: Path, xml_text: str, name: str = "F1.feature_result.xml") -> Path:
    path = tmp_path / name
    path.write_text(xml_text, encoding="utf-8")
    return path


#: Typical litellm-derived testcase properties (spec §6.3).
def cost_properties(*, cost: str = "0.0123", tokens: str = "12345", model: str = "deepseek-chat", exclude_tokens: str | None = None) -> dict[str, str]:
    props = {
        "usage_including_cached_inference.total_cost": cost,
        f"usage_including_cached_inference.{model}.total_cost": cost,
        f"usage_including_cached_inference.{model}.total_tokens": tokens,
        "Terminate": "yes",
    }
    if exclude_tokens is not None:
        props[f"usage_excluding_cached_inference.{model}.total_tokens"] = exclude_tokens
    return props


def parse_xml_text(xml_text: str) -> ET.Element:
    return ET.fromstring(xml_text)  # noqa: S314 - test fixture only


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def demo_server() -> Iterator[DemoServer]:
    """In-thread demo server on an ephemeral loopback port (spec §9 B 组)."""
    server = DemoServer(port=0)
    server.start(background=True)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def tmp_run_root(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "generated__F3__M3__s17" / "opt"


@pytest.fixture()
def sample_feature(tmp_path: Path) -> Path:
    path = tmp_path / "F3.feature"
    path.write_text('Feature: sample\n\nScenario: sample\n\nGiven I am on the page "http://127.0.0.1:8461/"\n', encoding="utf-8")
    return path
