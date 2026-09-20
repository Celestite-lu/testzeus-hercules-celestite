"""Shared fixtures/helpers for the distiller test-suite.

Everything here is offline: sample events are hand-written schema v1 JSON (spec §0) and the LLM
polisher is always replaced by :class:`FakePolisher` (spec §4.1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest
from record2gherkin.distiller import distill_events

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DEFAULT_ORIGIN = "https://staging.example.com"
STEP_KEYWORDS = ("Given ", "When ", "Then ", "And ", "But ")


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite offline (spec §7 criterion 1).

    Hercules' ``config`` module initialises a Sentry client when it is imported (``ENABLE_TELEMETRY``
    defaults to enabled), which then flushes queued events to sentry.io when the process exits. The
    flag is read at import time, hence it has to be set before the first test module imports it.
    """
    os.environ.setdefault("ENABLE_TELEMETRY", "0")


class FakePolisher:
    """Offline stand-in for the LLM polisher; records its calls for assertions."""

    instances: list["FakePolisher"] = []

    def __init__(
        self,
        response: str | None = None,
        *,
        transform: Callable[[str, Any], str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.transform = transform
        self.error = error
        self.calls: list[tuple[str, Any]] = []
        FakePolisher.instances.append(self)

    async def polish(self, skeleton_text: str, events: Mapping | list) -> str:
        self.calls.append((skeleton_text, events))
        if self.error is not None:
            raise self.error
        if self.transform is not None:
            return self.transform(skeleton_text, events)
        return self.response if self.response is not None else ""


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def fixture_names() -> list[str]:
    return sorted(path.name for path in FIXTURES_DIR.glob("*.json"))


def make_event(
    seq: int,
    event_type: str,
    *,
    target: Mapping[str, Any] | None = None,
    url: str | None = f"{DEFAULT_ORIGIN}/",
    page_title: str | None = "Staging Store",
    value: Any = None,
    assert_texts: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build one schema v1 event; ``target`` keys are merged over the schema defaults."""
    event: dict[str, Any] = {
        "seq": seq,
        "ts": 1695000000000 + seq,
        "type": event_type,
        "url": url,
        "page_title": page_title,
        "target": None,
        "value": value,
        "dom_snapshot": {"assert_texts": list(assert_texts or [])},
    }
    if target is not None:
        event["target"] = {
            "tag": None,
            "role": None,
            "name": None,
            "testid": None,
            "id": None,
            "ordinal": None,
            "form_label": None,
            **target,
        }
    event.update(extra)
    return event


def make_flow(events: list[dict[str, Any]], origin: str = DEFAULT_ORIGIN, **session: Any) -> dict[str, Any]:
    return {"session": {"started_at": "2026-09-20T12:00:00.000Z", "origin": origin, **session}, "events": list(events)}


def skeleton_of(events: Any, **kwargs: Any) -> str:
    """Skeleton text for a payload, without any polishing (deterministic path)."""
    return distill_events(events, **kwargs).skeleton_text


def step_lines(feature_text: str) -> list[str]:
    """Step lines of a feature text, stripped, in order (Feature/Scenario lines excluded)."""
    return [line.strip() for line in feature_text.splitlines() if line.strip().startswith(STEP_KEYWORDS)]


def single_step(feature_text: str) -> str:
    lines = step_lines(feature_text)
    assert len(lines) == 1, f"expected exactly one step, got {lines!r}"
    return lines[0]


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture()
def login_flow() -> dict[str, Any]:
    return load_fixture("login_search.json")


@pytest.fixture()
def form_flow() -> dict[str, Any]:
    return load_fixture("form_submit.json")


@pytest.fixture()
def sparse_flow() -> dict[str, Any]:
    return load_fixture("sparse_fields.json")


@pytest.fixture()
def empty_flow() -> dict[str, Any]:
    return load_fixture("empty_session.json")
