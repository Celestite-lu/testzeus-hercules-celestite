"""D 组：goal 预读与 Gherkin（spec §9 D14-D15）。

D14 ``sanitize_goal`` 纯函数表驱动；D15 ``render_feature`` 三段式 + 上游解析入口 1 Feature/1 Scenario。
真浏览器的预读行为在 ``test_browser_miniwob.py``（同组用例 16）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from record2gherkin.benchmark.goal_reader import (
    GoalReadError,
    goal_from_utterance,
    page_url,
    render_feature,
    sanitize_goal,
)
from testzeus_hercules.utils.gherkin_helper import split_feature_file

TASK_ID = "miniwob.click-test"
SUBDOMAIN = "click-test"
SEED = 1234567
PORT = 8462
EPISODE_MS = 240000


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('Click on the "button".', "Click on the 'button'."),
        ("line one\nline two", "line one line two"),
        ("  padded   text\twith\ttabs  ", "padded text with tabs"),
        ("multi\n\n\nnewline", "multi newline"),
        ("single 'quote' stays", "single 'quote' stays"),
        ("", ""),
        ('"', "'"),
    ],
)
def test_d14_sanitize_goal_is_a_pure_function(raw: str, expected: str) -> None:
    """D14：``"``→``'``、换行/连续空白折叠、strip。"""
    assert sanitize_goal(raw) == expected
    assert sanitize_goal(sanitize_goal(raw)) == expected  # 幂等


def test_d14_goal_from_utterance_handles_both_types() -> None:
    """D14 补充：str 与 dict（email-nl 类）两种 utterance 类型（review 必改 1.3）。"""
    assert goal_from_utterance("Do the thing.") == "Do the thing."
    assert goal_from_utterance({"utterance": 'Email "Raye" now', "fields": {"to": "x"}}) == "Email 'Raye' now"
    with pytest.raises(GoalReadError):
        goal_from_utterance({"fields": {}})
    with pytest.raises(GoalReadError):
        goal_from_utterance(None)
    with pytest.raises(GoalReadError):
        goal_from_utterance("   ")


def test_d15_render_feature_has_the_three_sections_and_the_seeded_url() -> None:
    """D15：三段式 + URL 含 ``r2g_seed``/``r2g_ms``；无占位符；恰 1 Feature/1 Scenario。"""
    text = render_feature(task_id=TASK_ID, subdomain=SUBDOMAIN, seed=SEED, port=PORT, episode_ms=EPISODE_MS, goal="Click on the 'button'.")
    assert text.splitlines() == [
        f"Feature: MiniWoB++ {TASK_ID}",
        f"  Scenario: {SUBDOMAIN} seed={SEED}",
        f'    Given I am on the page "http://127.0.0.1:{PORT}/miniwob/{SUBDOMAIN}.html?r2g_seed={SEED}&r2g_ms={EPISODE_MS}"',
        "    When Click on the 'button'.",
        "    Then the task should be completed successfully",
    ]
    assert text.count("Feature:") == 1
    assert text.count("Scenario:") == 1
    assert "{{TEST_DATA:" not in text and "<masked>" not in text
    assert page_url(SUBDOMAIN, port=PORT, seed=SEED, episode_ms=EPISODE_MS) in text


def test_d15_feature_parses_through_the_upstream_entry_point(tmp_path: Path) -> None:
    """D15：产物经 ``gherkin_helper.split_feature_file`` 解析通过（D1 同款校验）。"""
    text = render_feature(task_id=TASK_ID, subdomain=SUBDOMAIN, seed=SEED, port=PORT, episode_ms=EPISODE_MS, goal="Drag the shapes.")
    feature_path = tmp_path / f"miniwob__{SUBDOMAIN}__s{SEED}.feature"
    feature_path.write_text(text, encoding="utf-8")
    records = asyncio.run(split_feature_file(str(feature_path), str(tmp_path / "split")))
    assert len(records) == 1
    assert records[0]["feature"] == f"MiniWoB++ {TASK_ID}"
    assert records[0]["scenario"] == f"{SUBDOMAIN} seed={SEED}"


# ---------------------------------------------------------------------------------------------
# T8：模板上下文两变体（spec-r2 §7.2）
# ---------------------------------------------------------------------------------------------


def _render(**overrides: object) -> str:
    kwargs: dict[str, object] = dict(task_id=TASK_ID, subdomain=SUBDOMAIN, seed=SEED, port=PORT, episode_ms=EPISODE_MS, goal="Click on the 'button'.")
    kwargs.update(overrides)
    return render_feature(**kwargs)  # type: ignore[arg-type]


def test_t8_notes_off_keeps_the_r1_template_byte_for_byte() -> None:
    """T8：``notes=False``（默认，--template-notes off）与 r1 模板逐字节一致。"""
    assert _render() == (
        f"Feature: MiniWoB++ {TASK_ID}\n"
        f"  Scenario: {SUBDOMAIN} seed={SEED}\n"
        f'    Given I am on the page "http://127.0.0.1:{PORT}/miniwob/{SUBDOMAIN}.html?r2g_seed={SEED}&r2g_ms={EPISODE_MS}"\n'
        "    When Click on the 'button'.\n"
        "    Then the task should be completed successfully\n"
    )


def test_t8_notes_variant_snapshot_without_terminal_cue() -> None:
    """T8 变体 1：C7 on + C2 off → 恰 4 行注释；无 EPISODE ENDED 行（虚假信息禁出）。"""
    text = _render(notes=True, notes_terminal_cue=False)
    assert text.splitlines() == [
        f"Feature: MiniWoB++ {TASK_ID}",
        f"  Scenario: {SUBDOMAIN} seed={SEED}",
        f'    Given I am on the page "http://127.0.0.1:{PORT}/miniwob/{SUBDOMAIN}.html?r2g_seed={SEED}&r2g_ms={EPISODE_MS}"',
        "    When Click on the 'button'.",
        "  # Notes: The instruction above is also displayed at the top of the page (#query).",
        "  # This is a single-page task: do not reload or navigate away; all progress would be lost.",
        "  # The page shows no success or failure message. Once you have performed the requested",
        "  # action (including any required submit), report completion immediately.",
        "    Then the task should be completed successfully",
    ]
    assert "EPISODE ENDED" not in text
    assert "When Click on the 'button'." in text  # When 指令原文逐字不动


def test_t8_notes_variant_snapshot_with_terminal_cue() -> None:
    """T8 变体 2：C7 on + C2 on → 第 5 行 EPISODE ENDED 说明；注释整体仍在 Then 之前。"""
    text = _render(notes=True, notes_terminal_cue=True)
    lines = text.splitlines()
    assert lines[-1] == "    Then the task should be completed successfully"
    assert lines[-2] == ('  # A small "EPISODE ENDED" note in the corner means this episode is over (pass or fail alike): stop and report immediately.')
    assert lines[4:8] == [
        "  # Notes: The instruction above is also displayed at the top of the page (#query).",
        "  # This is a single-page task: do not reload or navigate away; all progress would be lost.",
        "  # The page shows no success or failure message. Once you have performed the requested",
        "  # action (including any required submit), report completion immediately.",
    ]
    assert text.count("EPISODE ENDED") == 1


def test_t8_terminal_cue_line_requires_notes(tmp_path: Path) -> None:
    """T8 补充：``notes=False`` 时 ``notes_terminal_cue`` 单独打开不产生任何注释；两变体均过 gherkin 解析。"""
    assert _render(notes=False, notes_terminal_cue=True) == _render()
    for text in (_render(notes=True, notes_terminal_cue=False), _render(notes=True, notes_terminal_cue=True)):
        feature_path = tmp_path / "variant.feature"
        feature_path.write_text(text, encoding="utf-8")
        records = asyncio.run(split_feature_file(str(feature_path), str(tmp_path / "split")))
        assert len(records) == 1
        assert records[0]["scenario"] == f"{SUBDOMAIN} seed={SEED}"
