"""A 组：任务表（spec §9 A1-A5）。

A1 125 行/唯一/字典序；A2 html 文件存在；A3 family/visual 派生；A4 seed 派生；A5 pilot 子集。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from record2gherkin.benchmark import tasks as tasks_module
from record2gherkin.benchmark.tasks import (
    EPISODE_MAX_TIME_MS_DEFAULT,
    EXPECTED_TASK_COUNT,
    PILOT_SUBDOMAINS,
    BenchmarkError,
    check_pilot_subdomains,
    derive_seed,
    load_tasks,
    make_run_id,
    select_stage,
)
from tests.record2gherkin.benchmark.conftest import (
    BENCHMARK_DIR,
    PROVENANCE_PATH,
    TASKS_PATH,
)


def test_a1_table_holds_125_unique_task_ids_in_dictionary_order(tasks: list[dict[str, Any]]) -> None:
    """A1：恰 125 行；task_id 唯一且 == ``miniwob.<subdomain>``；数组按 task_id 字典序。"""
    assert len(tasks) == EXPECTED_TASK_COUNT == 125
    ids = [task["task_id"] for task in tasks]
    assert len(set(ids)) == 125
    assert ids == sorted(ids)
    assert all(task["task_id"] == f"miniwob.{task['subdomain']}" for task in tasks)
    assert all(task["desc"] for task in tasks)


def test_a1_payload_metadata_is_locked(tasks: list[dict[str, Any]]) -> None:
    """A1 补充：顶层元数据（口径默认值、来源、ISO 时间戳）与模块常量一致。"""
    payload = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    assert payload["episode_max_time_ms_default"] == EPISODE_MAX_TIME_MS_DEFAULT == 240000
    assert payload["source"].startswith("browsergym-miniwob ")
    assert "ALL_MINIWOB_TASKS (125 tasks)" in payload["source"]
    assert payload["generated_at"]
    assert isinstance(payload["tasks"], list) and len(payload["tasks"]) == EXPECTED_TASK_COUNT


def test_a1_vendored_tree_and_provenance_are_present(html_root: Path) -> None:
    """A1 补充：vendored 资产与 PROVENANCE 逐项一致（文件数/字节数/全部 sha256，漂移检测）。"""
    files = sorted(path for path in html_root.rglob("*") if path.is_file())
    assert len(files) >= 300
    assert (html_root / "core" / "core.js").is_file()
    assert len(list((html_root / "miniwob").glob("*.html"))) == 130

    text = PROVENANCE_PATH.read_text(encoding="utf-8")
    assert "BSD-3-Clause" in text
    assert "github.com/Farama-Foundation/miniwob-plusplus" in text
    assert f"- files: {len(files)}" in text
    assert f"- total bytes: {sum(path.stat().st_size for path in files)}" in text

    recorded = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in text.splitlines() if re.fullmatch(r"[0-9a-f]{64}  \S+", line)}
    assert set(recorded) == {path.relative_to(html_root).as_posix() for path in files}
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == recorded[path.relative_to(html_root).as_posix()], f"vendored file drifted: {path}"


def test_a2_every_row_points_at_a_real_vendored_page(tasks: list[dict[str, Any]]) -> None:
    """A2：每行 ``html`` 文件真实存在且位于 ``miniwob_html/miniwob/<subdomain>.html``。"""
    for task in tasks:
        assert task["html"] == f"miniwob_html/miniwob/{task['subdomain']}.html"
        assert (BENCHMARK_DIR / task["html"]).is_file()


def test_a3_family_and_visual_derivation(tasks: list[dict[str, Any]]) -> None:
    """A3：``re.sub(r"-\\d+$", "")`` 只剥离末段纯数字；``visual`` 只看前缀；表中 visual 数 > 0。"""
    by_subdomain = {task["subdomain"]: task for task in tasks}
    assert by_subdomain["click-test-2"]["family"] == "click-test"
    assert by_subdomain["click-tab-2"]["family"] == "click-tab"
    assert by_subdomain["email-inbox-forward-nl-turk"]["family"] == "email-inbox-forward-nl-turk"
    for task in tasks:
        assert task["family"] == re.sub(r"-\d+$", "", task["subdomain"])
        assert task["visual"] is task["subdomain"].startswith("visual-")
    assert sum(1 for task in tasks if task["visual"]) > 0


def test_a4_seed_derivation_is_stable_within_a_task_and_unique_across_tasks(tasks: list[dict[str, Any]]) -> None:
    """A4：同 (exp_id, task_id) 多次恒等；全表枚举 seed 互异；公式字面复现。"""
    exp_id = "miniwob-pilot"
    seen: dict[int, str] = {}
    for task in tasks:
        seed = derive_seed(exp_id, task["task_id"])
        assert derive_seed(exp_id, task["task_id"]) == seed
        assert isinstance(seed, int) and 0 <= seed < 2**32
        assert seed not in seen, f"seed collision between {seen[seed]} and {task['task_id']}"
        seen[seed] = task["task_id"]
    assert len(seen) == EXPECTED_TASK_COUNT

    expected = int(hashlib.sha1(f"{exp_id}:miniwob:miniwob.click-test".encode("utf-8")).hexdigest()[:8], 16)
    assert derive_seed(exp_id, "miniwob.click-test") == expected
    assert derive_seed("other-exp", "miniwob.click-test") != expected
    assert make_run_id("click-test", expected) == f"miniwob__click-test__s{expected}"


def test_a5_pilot_subset_is_ten_tasks_over_five_families_with_a_visual_one(tasks: list[dict[str, Any]]) -> None:
    """A5：PILOT_SUBDOMAINS 恰 10 个、⊆ 全表、覆盖 ≥5 family、含 ≥1 visual。"""
    assert len(PILOT_SUBDOMAINS) == 10
    assert len(set(PILOT_SUBDOMAINS)) == 10
    by_subdomain = {task["subdomain"]: task for task in tasks}
    missing = [subdomain for subdomain in PILOT_SUBDOMAINS if subdomain not in by_subdomain]
    assert missing == []
    families = {by_subdomain[subdomain]["family"] for subdomain in PILOT_SUBDOMAINS}
    assert len(families) >= 5
    assert any(by_subdomain[subdomain]["visual"] for subdomain in PILOT_SUBDOMAINS)

    pilot = select_stage("pilot", tasks)
    assert [task["subdomain"] for task in pilot] == list(PILOT_SUBDOMAINS)
    assert len(select_stage("full", tasks)) == EXPECTED_TASK_COUNT
    check_pilot_subdomains(tasks)


def test_a5_missing_pilot_task_fails_fast(tasks: list[dict[str, Any]]) -> None:
    """A5 补充：上游改名/删任务 → 显式报错，不静默缩量（spec §2.3）。"""
    trimmed = [task for task in tasks if task["subdomain"] != "visual-addition"]
    with pytest.raises(BenchmarkError):
        check_pilot_subdomains(trimmed)
    with pytest.raises(BenchmarkError):
        select_stage("pilot", trimmed)
    with pytest.raises(BenchmarkError):
        select_stage("nope", tasks)


def test_load_tasks_rejects_a_damaged_table(tmp_path: Path, tasks: list[dict[str, Any]]) -> None:
    """A 组补充：读表校验（行数/唯一性/结构）失败即抛 ``BenchmarkError``，绝不静默缩量。"""
    short = tmp_path / "short.json"
    short.write_text(json.dumps({"tasks": tasks[:2]}), encoding="utf-8")
    with pytest.raises(BenchmarkError):
        load_tasks(short)

    duplicated = tmp_path / "dup.json"
    duplicated.write_text(json.dumps({"tasks": [tasks[0]] * EXPECTED_TASK_COUNT}), encoding="utf-8")
    with pytest.raises(BenchmarkError):
        load_tasks(duplicated)

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(BenchmarkError):
        load_tasks(broken)
    with pytest.raises(BenchmarkError):
        load_tasks(tmp_path / "missing.json")
