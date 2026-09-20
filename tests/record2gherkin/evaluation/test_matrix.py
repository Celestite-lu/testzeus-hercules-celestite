"""F 组：矩阵与预算护栏 + seed 派生（spec §9 F30-F31，spec §2.3/§7）。"""

from __future__ import annotations

import pytest
from record2gherkin.evaluation.metrics import FLOWS, METHODS, MUTATIONS
from record2gherkin.evaluation.sweep import (
    HERCULES_BUDGET_CAP,
    INFRA_RETRY_BUDGET,
    PILOT_CELLS,
    STAGE_HERCULES_RUNS,
    Cell,
    SweepError,
    assert_budget,
    derive_seed,
    hercules_budget,
    make_run_id,
    matrix,
    stage_cells,
)


def test_f30_matrix_is_60_cells_and_hercules_count_fits_the_cap() -> None:
    """F30 矩阵 6×5×2=60 格；Hercules 计数（30+4+5）= 39 ≤ 40 断言成立。"""
    cells = matrix()
    assert len(cells) == 60
    assert len(set(cells)) == 60
    assert {cell.method for cell in cells} == set(METHODS)
    assert len([cell for cell in cells if cell.method == "generated"]) == 30
    assert len([cell for cell in cells if cell.method == "baseline"]) == 30
    stage_sizes = {stage: len(stage_cells(stage)) for stage in ("pilot", "full", "baseline")}
    assert stage_sizes == {"pilot": 3, "full": 30, "baseline": 30}
    assert len(stage_cells("pilot")) < STAGE_HERCULES_RUNS["pilot"]  # pilot 预算含 1 次基础设施重试
    assert {cell.method for cell in stage_cells("baseline")} == {"baseline"}
    assert {(cell.flow, cell.mutation) for cell in stage_cells("pilot")} == set(PILOT_CELLS)

    budget = hercules_budget("full", "pilot")
    assert budget["breakdown"] == {"full": 30, "pilot": 4}
    assert budget["total"] == 30 + 4 + INFRA_RETRY_BUDGET == 39
    assert budget["cap"] == HERCULES_BUDGET_CAP == 40
    assert_budget(budget["total"])
    with pytest.raises(SweepError):
        assert_budget(HERCULES_BUDGET_CAP + 1)
    with pytest.raises(SweepError):
        stage_cells("nope")


def test_f31_seed_derivation_is_stable_within_a_cell_and_unique_across_cells() -> None:
    """F31 seed 派生（§2.3 公式）：同格多次派生恒等；同 exp 内任意两格派生互异。"""
    exp_id = "exp001"
    seen: dict[int, Cell] = {}
    for cell in matrix():
        seed = derive_seed(exp_id, cell.method, cell.flow, cell.mutation)
        assert derive_seed(exp_id, cell.method, cell.flow, cell.mutation) == seed
        assert isinstance(seed, int) and 0 <= seed < 2**32
        assert seed not in seen, f"seed collision between {seen.get(seed)} and {cell}"
        seen[seed] = cell
    assert len(seen) == 60
    # 不同 exp_id → 不同格坐标
    assert derive_seed("exp002", "generated", "F1", "M0") != derive_seed(exp_id, "generated", "F1", "M0")
    # method 参与派生（generated/baseline 同格不同 seed）
    assert derive_seed(exp_id, "generated", "F1", "M3") != derive_seed(exp_id, "baseline", "F1", "M3")
    # 公式字面复现：sha1("exp:method:flow:mutation")[:8]
    import hashlib

    expected = int(hashlib.sha1(f"{exp_id}:generated:F3:M3".encode("utf-8")).hexdigest()[:8], 16)
    assert derive_seed(exp_id, "generated", "F3", "M3") == expected


def test_run_id_embeds_the_final_seed() -> None:
    """补充：run_id 格式 ``<method>__<flow>__<mutation>__s<seed>``（§4.1），且不含循环引用。"""
    seed = derive_seed("exp001", "generated", "F3", "M3")
    run_id = make_run_id("generated", "F3", "M3", seed)
    assert run_id == f"generated__F3__M3__s{seed}"
    assert run_id.split("__") == ["generated", "F3", "M3", f"s{seed}"]


def test_stage_matrix_covers_every_flow_and_mutation() -> None:
    """补充：full/baseline 阶段覆盖 6×5，pilot 只跑 §7 指定格子。"""
    full = stage_cells("full")
    assert {(cell.flow, cell.mutation) for cell in full} == {(flow, mutation) for flow in FLOWS for mutation in MUTATIONS}
    assert len(stage_cells("baseline")) == len(FLOWS) * len(MUTATIONS)
