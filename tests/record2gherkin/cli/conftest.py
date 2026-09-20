"""Shared fixtures/helpers for the CLI test-suite (spec §8).

Everything here is offline: the Hercules sub-process is always monkeypatched away, JUnit trees are
synthesized by hand, the recorder is driven against a local ``file://`` fixture page and no API key is
ever read from disk (only temporary fake keys).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import pytest
from record2gherkin.evaluation.runner import (
    STATUS_FAILED,
    STATUS_NO_JUNIT,
    STATUS_PASSED,
    STATUS_TIMEOUT,
    RunResult,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
#: Recorder fixture page reused by the CLI record tests and by the demo script (spec §2.2).
DEMO_FORM_PATH = REPO_ROOT / "tests" / "record2gherkin" / "recorder" / "fixtures" / "demo_form.html"

#: Temporary fake key: never a real secret, long enough to exercise the fragment masking too.
FAKE_KEY = "dummy-key-0123456789"

DEFAULT_ORIGIN = "https://shop.example.com"
MASKED_SEQ = 6
#: Placeholder key produced for a masked input at ``seq=6`` (distiller spec §2.3).
MASKED_KEY = f"password_{MASKED_SEQ}"

#: S1 signature text (attributor rules S1: "max planner rounds exceeded", case-insensitive).
AGENT_LIMIT_MESSAGE = "Max planner rounds exceeded."

MIN_FEATURE_TEXT = 'Feature: Checkout\n\n  Scenario: pay with a saved card\n    Given I am on the page "https://shop.example.com/checkout"\n    Then I should see "Order placed successfully"\n'


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite offline: Hercules' telemetry defaults to on at import time."""
    os.environ.setdefault("ENABLE_TELEMETRY", "0")


@pytest.fixture(autouse=True)
def _capture_cli_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture the product logger's INFO lines into ``caplog`` for every test."""
    caplog.set_level(logging.INFO, logger="testzeus_hercules.utils.logger")


# ----------------------------------------------------------------------------------------------
# Synthetic run results and run directories (spec §8 conftest contract)
# ----------------------------------------------------------------------------------------------


def make_run_result(status: str, **overrides: Any) -> RunResult:
    """A :class:`RunResult` stand-in for the run-group tests; ``overrides`` win."""
    values: dict[str, Any] = {
        "run_id": "cli_20260920-120000_demo",
        "status": status,
        "passed": status == STATUS_PASSED,
        "duration_s": 12.5,
        "cost_usd": 0.0123,
        "total_tokens": 1234,
        "junit_xml": None,
        "failure_message": None,
        "run_dir": "/tmp/cli_runs/run",
    }
    values.update(overrides)
    return RunResult(**values)


def _testcase_xml(name: str, classname: str, failure_message: str | None) -> str:
    lines = [f'  <testcase name={quoteattr(name)} classname={quoteattr(classname)} time="1.5">']
    if failure_message is not None:
        lines.append(f"    <failure message={quoteattr(failure_message)}>{escape('Assertion with result: False')}</failure>")
    lines.append("  </testcase>")
    return "\n".join(lines)


def junit_xml(cases: Sequence[Mapping[str, Any]]) -> str:
    """Minimal JUnit tree: one testcase per entry, ``failure`` set marks it as failing."""
    failures = sum(1 for case in cases if case.get("failure") is not None)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<testsuite name="pytest" tests="{len(cases)}" errors="0" failures="{failures}" skipped="0" time="12.5">']
    lines.extend(_testcase_xml(str(case["name"]), str(case.get("classname", "Checkout")), case.get("failure")) for case in cases)
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def make_synth_run_dir(
    root: Path,
    *,
    failing: bool = True,
    failure_message: str = AGENT_LIMIT_MESSAGE,
    scenario: str = "test_demo_order",
    junit_rel: str = "output/demo_result.xml",
) -> Path:
    """Write a minimal run directory (JUnit + empty ``proofs/``, ``log_files/``) and return its root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "proofs").mkdir(exist_ok=True)
    (root / "log_files").mkdir(exist_ok=True)
    junit_path = root / junit_rel
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path.write_text(junit_xml([{"name": scenario, "failure": failure_message if failing else None}]), encoding="utf-8")
    return root


# ----------------------------------------------------------------------------------------------
# Sample event stream (schema v1, spec §8: includes one "<masked>" input)
# ----------------------------------------------------------------------------------------------


def _event(seq: int, event_type: str, *, target: Mapping[str, Any] | None = None, value: Any = None, url: str, page_title: str, assert_texts: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": 1695000000000 + seq * 1000,
        "type": event_type,
        "url": url,
        "page_title": page_title,
        "target": dict(target) if target is not None else None,
        "value": value,
        "dom_snapshot": {"assert_texts": list(assert_texts)},
    }


def sample_events() -> dict[str, Any]:
    """A hand-written schema v1 flow; the masked input sits at ``seq=6`` → key ``password_6``."""
    checkout = f"{DEFAULT_ORIGIN}/checkout"
    return {
        "session": {"started_at": "2026-09-20T12:00:00.000Z", "origin": DEFAULT_ORIGIN},
        "events": [
            _event(1, "navigate", url=checkout, page_title="Checkout"),
            _event(2, "input", url=checkout, page_title="Checkout", value="qa.user@example.com", target={"tag": "input", "role": "textbox", "name": "Email", "id": "email", "form_label": "Email"}),
            _event(
                3,
                "input",
                url=checkout,
                page_title="Checkout",
                value="wireless headphones",
                target={"tag": "input", "role": "textbox", "name": "Search products", "id": "search", "form_label": "Search"},
            ),
            _event(4, "select", url=checkout, page_title="Checkout", value="express", target={"tag": "select", "role": "combobox", "name": "Shipping", "id": "shipping", "form_label": "Shipping"}),
            _event(5, "click", url=checkout, page_title="Checkout", target={"tag": "button", "role": "button", "name": "Apply coupon", "id": "coupon-apply"}),
            _event(6, "input", url=checkout, page_title="Checkout", value="<masked>", target={"tag": "input", "role": "textbox", "name": "Password", "id": "password", "form_label": "Password"}),
            _event(7, "click", url=checkout, page_title="Checkout", target={"tag": "button", "role": "button", "name": "Place order", "id": "place-order"}, assert_texts=["Order placed successfully"]),
        ],
    }


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------------------------


@pytest.fixture()
def events_payload() -> dict[str, Any]:
    return sample_events()


@pytest.fixture()
def events_file(tmp_path: Path, events_payload: dict[str, Any]) -> Path:
    """The sample event stream on disk, default output path (``<stem>.feature``) exercised."""
    return write_json(tmp_path / "demo-events.json", events_payload)


@pytest.fixture()
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "LLM-Key.txt"
    path.write_text(FAKE_KEY + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def missing_key_file(tmp_path: Path) -> Path:
    return tmp_path / "no-such-key.txt"


@pytest.fixture()
def synth_run_dir_factory(tmp_path: Path) -> Callable[..., Path]:
    """``make_synth_run_dir`` bound to the test's tmp dir (unique sub-directory per call)."""
    counter = {"value": 0}

    def factory(**kwargs: Any) -> Path:
        counter["value"] += 1
        return make_synth_run_dir(tmp_path / f"run-{counter['value']}", **kwargs)

    return factory


@pytest.fixture()
def demo_form_url() -> str:
    return DEMO_FORM_PATH.as_uri()


__all__ = [
    "AGENT_LIMIT_MESSAGE",
    "DEFAULT_ORIGIN",
    "DEMO_FORM_PATH",
    "FAKE_KEY",
    "MASKED_KEY",
    "MASKED_SEQ",
    "MIN_FEATURE_TEXT",
    "STATUS_FAILED",
    "STATUS_NO_JUNIT",
    "STATUS_PASSED",
    "STATUS_TIMEOUT",
    "junit_xml",
    "make_run_result",
    "make_synth_run_dir",
    "sample_events",
    "write_json",
]
