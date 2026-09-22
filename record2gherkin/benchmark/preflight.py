"""C1c balance preflight (spec-r2 §3): a 1-token probe that gates every non-dry-run benchmark stage.

Hard gate (spec-r2 §0.1): the orchestrator probes the planner model (and, with ``--role-routing``,
the nav model too) *before* starting the patch server; any 402 / Insufficient Balance / connection
error aborts the run with exit 3 — no server, no cell, no burned key.

Transport discipline (review-r2 M4): the probe uses the same stack as the engine itself —
``langchain_openai.ChatOpenAI`` talking straight to ``LLM_MODEL_BASE_URL``.  litellm is deliberately
NOT used: custom endpoints need a provider prefix (``openai/...``) plus ``api_base``, so a bare model
name would fail permanently (flagging a usable key as dead) and success would prove nothing about the
engine path.

Secret discipline: the key travels as a function argument only, the probe writes no file, and every
failure detail goes through :func:`record2gherkin.evaluation.runner.mask_secret` before it is logged.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from record2gherkin.evaluation.runner import mask_secret

__all__ = ["ProbeResult", "probe_llm"]


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one probe: ``ok`` plus an already-masked human-readable detail."""

    ok: bool
    detail: str


def probe_llm(*, api_key: str, model: str, base_url: str, timeout_s: float = 30.0) -> ProbeResult:
    """One minimal (``max_tokens=1``) chat call over the engine's own transport (spec-r2 §3.1).

    Any exception — auth, balance, connection, timeout — becomes ``ok=False`` with the masked
    exception text; success returns ``ok=True``.  The probe never retries (``max_retries=0``) and
    never writes anything.
    """
    try:
        ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=1,
            timeout=timeout_s,
            max_retries=0,
        ).invoke([HumanMessage(content="ping")])
    except Exception as exc:  # noqa: BLE001 - any failure means "do not start the run"
        return ProbeResult(ok=False, detail=mask_secret(str(exc), api_key))
    return ProbeResult(ok=True, detail="ok")
