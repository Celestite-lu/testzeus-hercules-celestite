"""Recorded-event distillation: schema v1 event stream in, executable Gherkin out.

Public API:
    - :func:`distill_events` / :func:`distill_file` — synchronous entry points
    - :class:`DistillResult` / :class:`DistillError` — result and error types
    - :class:`Polisher` / :class:`DefaultPolisher` — optional LLM polishing hook
"""

from record2gherkin.distiller.api import (
    DistillError,
    DistillResult,
    distill_events,
    distill_file,
)
from record2gherkin.distiller.polisher import DefaultPolisher, Polisher

__all__ = ["DefaultPolisher", "DistillError", "DistillResult", "Polisher", "distill_events", "distill_file"]
