"""Public distillation API (spec §3): schema v1 events in, valid Gherkin out.

``distill_events`` / ``distill_file`` are synchronous; the optional polishing step is driven
internally with ``asyncio.run``. The skeleton is always kept in the result, and every polishing
failure degrades to the skeleton plus a ``fallback_reason`` (spec §5.3).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from record2gherkin.distiller.events import Facts, ParsedInput, parse_input
from record2gherkin.distiller.factcheck import validate_polished, validate_skeleton
from record2gherkin.distiller.polisher import Polisher, clean_polished_output
from record2gherkin.distiller.templates import Skeleton, build_skeleton
from testzeus_hercules.utils.logger import logger


class DistillError(Exception):
    """Unrecoverable distillation error (skeleton self-check failure, unreadable input file)."""


@dataclass
class DistillResult:
    """Distillation outcome; ``feature_text`` is the polished text when it was accepted, else the skeleton."""

    feature_text: str
    skeleton_text: str
    output_path: str | None = None
    used_llm_polish: bool = False
    fallback_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def _build_skeleton_checked(events: Any, test_data_values: Mapping[str, Any] | None) -> tuple[Skeleton, list[str], ParsedInput]:
    """Parse, render and self-check the skeleton (spec §2.5: a self-check failure is a bug, never a fallback)."""
    parsed = parse_input(events, test_data_values=test_data_values)
    skeleton = build_skeleton(parsed)
    report = validate_skeleton(skeleton.text, parsed.facts)
    if not report.ok:
        raise DistillError(f"skeleton self-check failed on valid input (bug): {report.fallback_reason()}")
    warnings = list(parsed.warnings) + list(skeleton.warnings)
    return skeleton, warnings, parsed


def _apply_polisher(polisher: Polisher, skeleton_text: str, events: Any, facts: Facts) -> tuple[str, bool, str | None]:
    """Run the polisher and validate its output; returns ``(feature_text, used_llm_polish, fallback_reason)``."""
    try:
        raw = asyncio.run(polisher.polish(skeleton_text, events))
    except Exception as exc:  # spec §4.1: any polisher failure degrades to the skeleton
        logger.warning("distiller: polisher failed (%s): %s", type(exc).__name__, exc)
        return skeleton_text, False, f"polish_error:{type(exc).__name__}"

    polished = clean_polished_output(raw)  # spec §4.3.1
    if not polished.strip():
        logger.warning("distiller: polisher returned an empty feature; falling back to skeleton")
        return skeleton_text, False, "polish_empty"

    report = validate_polished(polished, skeleton_text, facts)  # spec §4.3.2-4
    if report.ok:
        return polished, True, None
    logger.warning("distiller: polished feature rejected (%s); falling back to skeleton", report.fallback_reason())
    return skeleton_text, False, report.fallback_reason()


def distill_events(
    events: Mapping | list,
    polisher: Polisher | None = None,
    test_data_values: Mapping[str, str] | None = None,
) -> DistillResult:
    """Distil a parsed events payload into Gherkin (spec §3).

    ``polisher=None`` keeps the run fully deterministic and model-free; ``test_data_values`` fills the
    ``{{TEST_DATA:password_<seq>}}`` placeholders generated for masked recorded values (spec §2.3).
    """
    skeleton, warnings, parsed = _build_skeleton_checked(events, test_data_values)
    if polisher is None:
        return DistillResult(feature_text=skeleton.text, skeleton_text=skeleton.text, warnings=warnings)

    feature_text, used_llm_polish, fallback_reason = _apply_polisher(polisher, skeleton.text, events, parsed.facts)
    return DistillResult(
        feature_text=feature_text,
        skeleton_text=skeleton.text,
        used_llm_polish=used_llm_polish,
        fallback_reason=fallback_reason,
        warnings=warnings,
    )


def default_output_path(events_path: str) -> str:
    """``<events dir>/<events stem>.feature`` (spec §3: default output path of ``distill_file``)."""
    stem, _ = os.path.splitext(events_path)
    return f"{stem}.feature"


def distill_file(
    events_path: str,
    output_path: str | None = None,
    polisher: Polisher | None = None,
    test_data_values: Mapping[str, str] | None = None,
) -> DistillResult:
    """Read a schema v1 events JSON file, distil it and write the resulting feature file (spec §3)."""
    try:
        with open(events_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise DistillError(f"cannot read events JSON {events_path!r}: {exc}") from exc

    result = distill_events(payload, polisher=polisher, test_data_values=test_data_values)

    target = output_path or default_output_path(events_path)
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(result.feature_text)
    result.output_path = target
    return result
