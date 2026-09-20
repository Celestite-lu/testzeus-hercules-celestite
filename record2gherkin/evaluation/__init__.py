"""Evaluation framework for the record-to-Gherkin UI mutation experiment (spec §0).

Modules:
    - :mod:`demo_app` — MiniShop registry, renderer and the M0-M4 mutation engine
    - :mod:`demo_server` — local HTTP server exposing one document per ``(mutation, seed)``
    - :mod:`record_flows` — D1录制：六条流程 -> schema v1 event JSON
    - :mod:`distill_batch` — D1 蒸馏：event JSON -> feature（纯模板，无 LLM）
    - :mod:`runner` — Hercules sub-process executor + JUnit parsing + key masking
    - :mod:`baseline` — property-selector Playwright baseline (spec §5 口径)
    - :mod:`metrics` — first-pass rate / survival table / cost aggregation
    - :mod:`sweep` — experiment matrix, seed derivation, budget guard and the sweep CLI

Nothing in this package modifies ``testzeus_hercules/``: it drives Hercules strictly as a
sub-process with an injected environment.
"""

from __future__ import annotations

__all__: list[str] = []
