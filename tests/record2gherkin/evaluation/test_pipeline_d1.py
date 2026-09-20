"""D1 管线验收（spec §10.3）：录制产物 → 蒸馏 → 上游可解析。

依赖 ``dev_runs/experiments/recordings``（gitignore 的实验产物）：产物不存在时跳过，并在跳过
原因里给出重建命令。全程离线：纯模板蒸馏，无 LLM。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from record2gherkin.distiller import distill_events
from record2gherkin.evaluation.distill_batch import _feature_problems
from record2gherkin.evaluation.record_flows import (
    EVENT_KEYS,
    EVENT_TYPES,
    EXPECTED_ASSERTIONS,
    FLOWS,
)
from tests.record2gherkin.evaluation.conftest import RECORDINGS_DIR
from testzeus_hercules.utils.gherkin_helper import split_feature_file


def _recording_path(flow: str) -> Path:
    return RECORDINGS_DIR / f"{flow}.json"


pytestmark = pytest.mark.skipif(
    not RECORDINGS_DIR.is_dir(),
    reason=f"recordings not present at {RECORDINGS_DIR}; rebuild with `uv run python -m record2gherkin.evaluation.record_flows`",
)


@pytest.mark.parametrize("flow", FLOWS)
def test_d1_recording_satisfies_schema_and_assertion_table(flow: str) -> None:
    """D1 录制：schema v1 字段/类型合法，且 §1.3 的 Then 断言文本已被捕获。"""
    path = _recording_path(flow)
    if not path.is_file():
        pytest.skip(f"recording missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"session", "events"}
    events = payload["events"]
    assert events and events[0]["type"] == "navigate"
    for event in events:
        assert EVENT_KEYS <= set(event)
        assert event["type"] in EVENT_TYPES
    captured = [text for event in events for text in event["dom_snapshot"]["assert_texts"]]
    for expected in EXPECTED_ASSERTIONS[flow]:
        assert any(expected in text for text in captured), f"{flow}: missing {expected!r} in {captured!r}"


@pytest.mark.parametrize("flow", FLOWS)
def test_d1_feature_is_one_parseable_scenario(flow: str, tmp_path: Path) -> None:
    """D1 蒸馏：恰 1 Feature/1 Scenario，``split_feature_file`` 解析通过，无 TEST_DATA/<masked>。"""
    path = _recording_path(flow)
    if not path.is_file():
        pytest.skip(f"recording missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = distill_events(payload)  # 无 polisher：纯模板，无 LLM
    assert result.used_llm_polish is False
    assert _feature_problems(result.feature_text) == []
    feature_path = tmp_path / f"{flow}.feature"
    feature_path.write_text(result.feature_text, encoding="utf-8")
    records = asyncio.run(split_feature_file(str(feature_path), str(tmp_path / "split")))
    assert len(records) == 1
