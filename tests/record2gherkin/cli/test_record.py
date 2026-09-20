"""Group B — ``record``: bookmarklet mode and the Playwright recording loop (spec §8 cases 4-7).

The browser tests run real headless Chromium against the recorder module's ``demo_form.html`` fixture
(``file://``), exactly like the recorder test-suite; the stop condition is injected through the
``_stop_when_for`` test seam (spec §2.1).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from record2gherkin import cli


def _inject_stop_when(monkeypatch: pytest.MonkeyPatch, predicate: Callable[[Any], bool]) -> None:
    """Replace the production poll predicate with a test closure that gets the live page."""
    monkeypatch.setattr(cli, "_stop_when_for", lambda args: predicate)


def test_manual_mode_prints_bookmarklet_usage(monkeypatch, caplog) -> None:
    """Case 4: ``--manual`` prints the bookmarklet path and the four usage steps, no browser."""
    started: list[str] = []
    monkeypatch.setattr(cli, "record_events", lambda *args, **kwargs: started.append("record_events") or cli.EXIT_OK)

    assert cli.main(["record", "--manual"]) == cli.EXIT_OK
    assert started == []
    text = caplog.text
    assert str(cli.BOOKMARKLET_PATH.resolve()) in text
    assert "R2GRecorder.copy()" in text
    assert "只注入一次" in text


def test_record_fixture_roundtrip(tmp_path: Path, monkeypatch, demo_form_url: str) -> None:
    """Case 5: two real clicks drive the recording; the artefact is a parseable event stream."""
    out = tmp_path / "nested" / "events.json"

    def drive(page: Any) -> bool:
        page.click("#show-result")
        page.click("#newsletter")
        return True

    _inject_stop_when(monkeypatch, drive)

    assert cli.main(["record", demo_form_url, "--headless", "--out", str(out)]) == cli.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload) == {"session", "events"}
    assert isinstance(payload["events"], list)
    assert len(payload["events"]) >= 3
    assert payload["events"][0]["type"] == "navigate"
    assert isinstance(payload["session"]["origin"], str)
    assert [event["seq"] for event in payload["events"]] == list(range(1, len(payload["events"]) + 1))


def test_record_empty_recording_warns(tmp_path: Path, monkeypatch, demo_form_url: str, caplog) -> None:
    """Case 6: stopping before any interaction leaves only the initial navigate event (R1 semantics)."""
    out = tmp_path / "events.json"
    _inject_stop_when(monkeypatch, lambda page: True)

    assert cli.main(["record", demo_form_url, "--headless", "--out", str(out)]) == cli.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [event["type"] for event in payload["events"]] == ["navigate"]
    assert "empty_recording" in caplog.text


def test_record_unreachable_url_exit_2(tmp_path: Path, caplog) -> None:
    """Case 7: a refused connection is an input error; nothing is written and no key is involved.

    ``--max-duration`` only bounds the test run in case something on the machine answers on the closed
    loopback port; with the port unbound the failure is immediate.
    """
    out = tmp_path / "events.json"

    assert cli.main(["record", "http://127.0.0.1:1/", "--headless", "--max-duration", "2", "--out", str(out)]) == cli.EXIT_USAGE

    assert not out.exists()
    assert "cannot open http://127.0.0.1:1/" in caplog.text


def test_record_closed_page_exit_2(tmp_path: Path, monkeypatch, demo_form_url: str, caplog) -> None:
    """Spec §7: a page that disappears mid-recording aborts with exit 2 and saves nothing."""
    out = tmp_path / "events.json"

    def close_page(page: Any) -> bool:
        page.close()
        return False

    _inject_stop_when(monkeypatch, close_page)

    assert cli.main(["record", demo_form_url, "--headless", "--out", str(out)]) == cli.EXIT_USAGE

    assert not out.exists()
    assert "页面已关闭" in caplog.text


def test_record_max_duration_stops_recording(tmp_path: Path, demo_form_url: str, caplog) -> None:
    """Spec §1.1: ``--max-duration`` ends an unattended recording and still saves the artefact."""
    out = tmp_path / "events.json"

    assert cli.main(["record", demo_form_url, "--headless", "--max-duration", "0.5", "--out", str(out)]) == cli.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [event["type"] for event in payload["events"]] == ["navigate"]
    assert "empty_recording" in caplog.text
