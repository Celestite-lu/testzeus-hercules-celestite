"""Goal pre-reading and Gherkin rendering (spec §4/§5).

The episode is seeded from the URL, so the harness can open the *same* episode in its own browser,
read ``core.getUtterance()`` and bake that text into the feature file the executor will replay.  The
pre-read page is closed immediately and never calls ``endEpisode`` — no reward record is produced.

The utterance is not always a string: the email family returns ``{"utterance": ..., "fields": ...}``,
handled the same way as browsergym's ``base.py::_get_goal`` (review 必改 1.3).
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

#: spec §4.1 defaults: 15s for ``WOB_TASK_READY``, 10s for the utterance, capped by ``timeout_s``.
DEFAULT_TIMEOUT_S = 30.0
TASK_READY_TIMEOUT_MS = 15000
UTTERANCE_TIMEOUT_MS = 10000

TASK_READY_CONDITION = "() => window.WOB_TASK_READY === true"
UTTERANCE_CONDITION = "() => typeof core !== 'undefined' && !!core.getUtterance()"
UTTERANCE_EXPRESSION = "core.getUtterance()"
START_ERROR_EXPRESSION = "() => window.__R2G_START_ERROR || null"


class GoalReadError(RuntimeError):
    """The pre-read page did not yield an utterance; the cell is recorded as ``no_goal`` (spec §4.1)."""


def page_url(subdomain: str, *, port: int, seed: int, episode_ms: int) -> str:
    """The single URL shape of the benchmark (spec §5.1): host, seed and episode budget in the query."""
    return f"http://127.0.0.1:{port}/miniwob/{subdomain}.html?r2g_seed={seed}&r2g_ms={episode_ms}"


def sanitize_goal(text: str) -> str:
    """``"`` → ``'``, every whitespace run (newlines included) → one space, then strip (spec §4.2)."""
    return " ".join(str(text).replace('"', "'").split())


def goal_from_utterance(value: Any) -> str:
    """Normalise ``core.getUtterance()``: plain str, or the ``{"utterance": ...}`` dict (spec §4.1)."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, Mapping) and isinstance(value.get("utterance"), str):
        text = str(value["utterance"])
    else:
        raise GoalReadError(f"unsupported utterance payload: {type(value).__name__}")
    goal = sanitize_goal(text)
    if not goal:
        raise GoalReadError("empty utterance")
    return goal


def _remaining_ms(deadline: float) -> int:
    return max(1, int((deadline - time.monotonic()) * 1000))


def _read_utterance(url: str, *, deadline: float, subdomain: str, seed: int) -> Any:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=_remaining_ms(deadline))
            page.wait_for_function(TASK_READY_CONDITION, timeout=min(TASK_READY_TIMEOUT_MS, _remaining_ms(deadline)))
            try:
                page.wait_for_function(UTTERANCE_CONDITION, timeout=min(UTTERANCE_TIMEOUT_MS, _remaining_ms(deadline)))
            except PlaywrightTimeoutError:
                start_error = page.evaluate(START_ERROR_EXPRESSION)
                if start_error:
                    raise GoalReadError(f"auto-start failed for {subdomain} seed={seed}: {start_error}") from None
                raise
            return page.evaluate(UTTERANCE_EXPRESSION)
        finally:
            browser.close()


def read_goal(subdomain: str, seed: int, *, port: int, episode_ms: int, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """Open ``(subdomain, seed)``, read the utterance and return it sanitized (spec §4.1).

    ``timeout_s`` bounds the whole pre-read (navigation + both waits).  Any failure — page error,
    missing utterance, empty text — becomes :class:`GoalReadError`; the cell is then recorded as
    ``no_goal`` and the run continues with the next cell.
    """
    url = page_url(subdomain, port=port, seed=seed, episode_ms=episode_ms)
    deadline = time.monotonic() + float(timeout_s)
    try:
        value = _read_utterance(url, deadline=deadline, subdomain=subdomain, seed=seed)
    except GoalReadError:
        raise
    except (PlaywrightError, OSError, ValueError) as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else ""
        raise GoalReadError(f"cannot read goal for {subdomain} seed={seed}: {type(exc).__name__}: {first_line}") from exc
    return goal_from_utterance(value)


def render_feature(*, task_id: str, subdomain: str, seed: int, port: int, episode_ms: int, goal: str) -> str:
    """The single feature-file shape of the benchmark (spec §5.1, verbatim template).

    ``goal`` is expected to be sanitized already (:func:`sanitize_goal`); ``Then`` is the planner's
    intent statement only — the official verdict comes from the page reward (spec §0 口径 4).
    """
    url = page_url(subdomain, port=port, seed=seed, episode_ms=episode_ms)
    return (
        f"Feature: MiniWoB++ {task_id}\n" f"  Scenario: {subdomain} seed={seed}\n" f'    Given I am on the page "{url}"\n' f"    When {goal}\n" f"    Then the task should be completed successfully\n"
    )
