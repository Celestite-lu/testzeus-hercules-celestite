"""Optional LLM polishing layer: protocol, prompt building and the default LiteLLM implementation (spec §4).

The Hercules/LiteLLM imports are performed lazily inside the functions that need them, so importing
this module never touches the network or the LLM configuration (spec §4.1 offline constraint).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping, Protocol

from testzeus_hercules.utils.logger import logger

#: Weak-model role key used for polishing (spec §4.1, PLAN §3.3 routing table).
DEFAULT_POLISH_ROLE = "distiller_polish"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_TIMEOUT_SECONDS = 60.0
DIGEST_VALUE_MAX_LENGTH = 120

SYSTEM_PROMPT = (
    "You polish Gherkin/BDD feature files that were generated from a recorded browser session. "
    "You never change what the test does.\n"
    "Allowed edits:\n"
    "1. Merge consecutive input steps that target the same field into a single step, keeping the final value.\n"
    "2. Improve the wording of steps (English).\n"
    "3. Rename the Feature and Scenario titles (English).\n"
    "Hard constraints:\n"
    "- Never add, remove or reword any Then step.\n"
    "- Never add, change or delete any quoted literal value.\n"
    "- Never reorder steps, and never change their Given/When/Then meaning.\n"
    "- Emit exactly one Feature line and exactly one Scenario line.\n"
    "- Write every line at column 0, without indentation.\n"
    "- Output only the feature file body: no explanation, no markdown fences."
)

USER_PROMPT_TEMPLATE = "Skeleton feature file:\n" "{skeleton}\n\n" "Recorded events (one per line, format seq|type|target.name|value):\n" "{digest}\n\n" "Return the polished feature file."


class Polisher(Protocol):
    """Polishing hook (spec §3): async, so the default implementation can talk to LiteLLM."""

    async def polish(self, skeleton_text: str, events: Mapping | list) -> str: ...


def clean_polished_output(raw: Any) -> str:
    """Strip markdown fences/preamble from a polishing response (spec §4.1/§4.3.1)."""
    from testzeus_hercules.utils.gherkin_generator import clean_gherkin_output

    return clean_gherkin_output(raw if isinstance(raw, str) else str(raw))


def _iter_raw_events(events: Any) -> list[Any]:
    if isinstance(events, Mapping):
        raw_events = events.get("events")
        return list(raw_events) if isinstance(raw_events, list) else []
    if isinstance(events, list):
        return list(events)
    return []


def build_event_digest(events: Mapping | list) -> str:
    """Compact event listing for the prompt (spec §4.2.4: one ``seq|type|target.name|value`` per line)."""
    rows: list[str] = []
    for position, raw in enumerate(_iter_raw_events(events)):
        if not isinstance(raw, Mapping):
            continue
        seq = raw.get("seq", position + 1)
        target = raw.get("target") if isinstance(raw.get("target"), Mapping) else {}
        name = target.get("name")
        rows.append(
            "|".join(
                (
                    str(seq),
                    str(raw.get("type", "?")),
                    " ".join(str(name).split()) if name else "-",
                    " ".join(str(raw.get("value")).split())[:DIGEST_VALUE_MAX_LENGTH] if raw.get("value") is not None else "-",
                )
            )
        )
    return "\n".join(rows) if rows else "(no events)"


def build_polish_prompt(skeleton_text: str, events: Mapping | list) -> list[dict[str, str]]:
    """Chat messages for one polishing call (spec §4.2)."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(skeleton=skeleton_text.strip(), digest=build_event_digest(events)),
        },
    ]


def _default_get_model(role: str, temperature: float) -> Any:
    """Build the weak-model role through Hercules' LiteLLM wiring, pinning the temperature (spec §4.1)."""
    from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

    model = get_litellm_chat_model(role)
    if hasattr(model, "temperature"):
        model.temperature = temperature
    return model


def _response_text(response: Any) -> str:
    """Best-effort text extraction from a LangChain chat response."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


class DefaultPolisher:
    """LiteLLM-backed polisher: weak-model role, temperature 0, at most 2 attempts, 60s each (spec §4.1).

    Failures are raised to the caller: the fallback decision belongs to the API layer (spec §4.1).
    """

    def __init__(
        self,
        get_model: Callable[[str, float], Any] | None = None,
        *,
        role: str = DEFAULT_POLISH_ROLE,
        temperature: float = DEFAULT_TEMPERATURE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._get_model = get_model or _default_get_model
        self._role = role
        self._temperature = temperature
        self._max_attempts = max(1, int(max_attempts))
        self._timeout_seconds = timeout_seconds

    async def polish(self, skeleton_text: str, events: Mapping | list) -> str:
        """Return the cleaned polished feature text; raises on transport/model failures."""
        messages = build_polish_prompt(skeleton_text, events)
        model = self._get_model(self._role, self._temperature)
        last_error: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await asyncio.wait_for(model.ainvoke(messages), timeout=self._timeout_seconds)
            except Exception as exc:
                last_error = exc
                logger.warning("distiller polish attempt %s/%s failed (%s): %s", attempt, self._max_attempts, type(exc).__name__, exc)
                continue
            text = clean_polished_output(_response_text(response))
            if text.strip():
                return text
            last_error = None
            logger.warning("distiller polish attempt %s/%s returned empty output", attempt, self._max_attempts)
        if last_error is not None:
            raise last_error
        return ""
