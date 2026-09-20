"""D1 distillation batch: schema v1 recordings in, one parseable feature per flow out.

Skeleton-only (``polisher=None``): deterministic, model-free, offline.  Each feature must contain
exactly one ``Feature:`` and one ``Scenario:``, must be parseable by the upstream
``split_feature_file`` and must not carry ``{{TEST_DATA:`` placeholders or ``<masked>`` literals —
the acceptance criteria of spec §10.3.

CLI: ``uv run python -m record2gherkin.evaluation.distill_batch [--recordings-dir DIR] [--features-dir DIR]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# 上游 config/telemetry 在 import 时初始化 Sentry；CLI 也必须在 import 之前压掉（spec §4.3）。
os.environ.setdefault("ENABLE_TELEMETRY", "0")

from record2gherkin.distiller import DistillError, distill_file  # noqa: E402
from record2gherkin.evaluation.sweep import (  # noqa: E402
    DEFAULT_FEATURES_DIR,
    DEFAULT_RECORDINGS_DIR,
)
from testzeus_hercules.utils.gherkin_helper import split_feature_file  # noqa: E402
from testzeus_hercules.utils.logger import logger  # noqa: E402

FORBIDDEN_SNIPPETS = ("{{TEST_DATA:", "<masked>")


@dataclass(frozen=True)
class DistillSummary:
    """Outcome of distilling one recording."""

    flow: str
    events_json: Path
    feature_path: Path
    feature_lines: int
    steps: int
    scenario_count: int
    split_scenario_count: int
    warnings: tuple[str, ...]
    used_llm_polish: bool


def distill_recordings(
    *,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
    features_dir: Path = DEFAULT_FEATURES_DIR,
    flows: Sequence[str] | None = None,
    split_check_dir: Path | None = None,
) -> list[DistillSummary]:
    """Distil every ``<recordings_dir>/<flow>.json`` into ``<features_dir>/<flow>.feature``."""
    source_dir = Path(recordings_dir)
    target_dir = Path(features_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    check_dir = Path(split_check_dir) if split_check_dir is not None else target_dir / "_split_check"
    check_dir.mkdir(parents=True, exist_ok=True)

    names = list(flows) if flows is not None else sorted(path.stem for path in source_dir.glob("*.json"))
    if not names:
        raise DistillError(f"no recordings found in {source_dir}")

    summaries: list[DistillSummary] = []
    for flow in names:
        events_path = source_dir / f"{flow}.json"
        if not events_path.is_file():
            raise DistillError(f"recording not found: {events_path}")
        feature_path = target_dir / f"{flow}.feature"
        result = distill_file(str(events_path), output_path=str(feature_path))
        feature_text = result.feature_text
        problems = _feature_problems(feature_text)
        if problems:
            raise DistillError(f"{flow}: distilled feature rejected: {'; '.join(problems)}")
        split_records = asyncio.run(split_feature_file(str(feature_path), str(check_dir / flow)))
        summary = DistillSummary(
            flow=flow,
            events_json=events_path,
            feature_path=feature_path,
            feature_lines=len([line for line in feature_text.splitlines() if line.strip()]),
            steps=len([line for line in feature_text.splitlines() if line.strip().startswith(("Given ", "When ", "Then ", "And ", "But "))]),
            scenario_count=len([line for line in feature_text.splitlines() if line.strip().startswith("Scenario:")]),
            split_scenario_count=len(split_records),
            warnings=tuple(result.warnings),
            used_llm_polish=result.used_llm_polish,
        )
        if summary.scenario_count != 1 or summary.split_scenario_count != 1:
            raise DistillError(f"{flow}: expected exactly 1 scenario, got text={summary.scenario_count} split={summary.split_scenario_count}")
        if summary.used_llm_polish:
            raise DistillError(f"{flow}: skeleton-only distillation must not use the LLM polisher")
        summaries.append(summary)
        logger.info(
            "distill_batch: %s -> %s (%s scenario, %s steps, warnings=%s)",
            flow,
            feature_path,
            summary.split_scenario_count,
            summary.steps,
            ",".join(summary.warnings) or "-",
        )
    return summaries


def _feature_problems(feature_text: str) -> list[str]:
    problems: list[str] = []
    features = [line for line in feature_text.splitlines() if line.strip().startswith("Feature:")]
    scenarios = [line for line in feature_text.splitlines() if line.strip().startswith("Scenario:")]
    if len(features) != 1:
        problems.append(f"expected exactly 1 Feature line, found {len(features)}")
    if len(scenarios) != 1:
        problems.append(f"expected exactly 1 Scenario line, found {len(scenarios)}")
    for snippet in FORBIDDEN_SNIPPETS:
        if snippet in feature_text:
            problems.append(f"forbidden snippet present: {snippet}")
    return problems


def first_lines(text: str, limit: int = 200) -> str:
    """Single-line digest of a feature for logs/reports."""
    joined = " / ".join(line.strip() for line in text.splitlines() if line.strip())
    return joined[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="distil the six D1 recordings into features")
    parser.add_argument("--recordings-dir", default=str(DEFAULT_RECORDINGS_DIR))
    parser.add_argument("--features-dir", default=str(DEFAULT_FEATURES_DIR))
    args = parser.parse_args(argv)

    try:
        summaries = distill_recordings(recordings_dir=Path(args.recordings_dir), features_dir=Path(args.features_dir))
    except DistillError as exc:
        logger.error("distill_batch: %s", exc)
        return 2
    for summary in summaries:
        logger.info("distill_batch: %s", json.dumps({"flow": summary.flow, "feature": str(summary.feature_path), "steps": summary.steps}, ensure_ascii=False))
    logger.info("distill_batch: %s features distilled into %s", len(summaries), args.features_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
