"""Failure attributor: a failed Hercules run in, a trustworthy category plus verifiable evidence out.

Pipeline (spec §2-§6): evidence loader -> deterministic signature rules -> (only for ambiguous cases)
LLM summary layer with mandatory citation back-check -> JSON/Markdown report.

Public API:
    - :func:`attribute_run` / :func:`attribute_case` — synchronous entry points
    - :class:`AttributionResult` — result with ``to_json()`` / ``to_markdown()``
    - :class:`AttributionError` / :data:`Category` — error type and category enum
    - :class:`DefaultAttributorAnalyzer` / :class:`LlmAnalyzer` — optional LLM hook
"""

from record2gherkin.attributor.api import (
    AttributionError,
    attribute_case,
    attribute_run,
)
from record2gherkin.attributor.evidence import Category
from record2gherkin.attributor.llm_layer import DefaultAttributorAnalyzer, LlmAnalyzer
from record2gherkin.attributor.report import AttributionResult

__all__ = [
    "AttributionError",
    "AttributionResult",
    "Category",
    "DefaultAttributorAnalyzer",
    "LlmAnalyzer",
    "attribute_case",
    "attribute_run",
]
