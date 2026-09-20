"""Shared fixtures/helpers for the attributor test-suite (spec §7).

Everything here is offline: run directories are synthesized on the fly (hand-written minimal JUnit
tree, ``agent_inner_thoughts.json`` in both content shapes, 0-byte PNGs) and the LLM layer is always
driven by :class:`FakeAnalyzer`. No network, no LLM key, no browser.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import pytest
from record2gherkin.attributor.evidence import (
    EvidenceBundle,
    TestCaseEvidence,
    load_bundle,
)
from record2gherkin.attributor.llm_layer import LlmAnalysisOutput, LlmAnalysisRequest
from record2gherkin.attributor.rules import RuleOutcome, apply_rules

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_RUN_DIR = FIXTURES_DIR / "sample_run"

DEFAULT_FEATURE = (
    'Feature: Checkout\n\n  Scenario: pay with a saved card\n    Given the user is on the checkout page\n    When the user clicks "Confirm order"\n    Then the confirmation page is shown\n'
)
DEFAULT_FEATURE_REL = "gherkin_files/checkout.feature"
DEFAULT_THOUGHTS_REL = "log_files/stake1/run1/agent_inner_thoughts.json"
DEFAULT_SCREENSHOTS_REL = "proofs/stake1/run1/screenshots"
DEFAULT_JUNIT_REL = "output/junit.xml"

TEXT_KINDS = ("junit_failure", "junit_sysout", "junit_property", "thoughts", "feature_file")

#: JUnit property names (the base-folder key keeps the upstream typo, spec §2.1).
PROP_PROOFS_SCREENSHOT = "Proofs Screenshot"
PROP_PROOFS_BASE_FOLDER = "Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"
PROP_PLANNER_THOUGHTS = "Planner Thoughts"
PROP_FEATURE_FILE = "Feature File"


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite offline/quiet: Hercules' telemetry defaults to on at import time."""
    os.environ.setdefault("ENABLE_TELEMETRY", "0")


# ----------------------------------------------------------------------------------------------
# Run-directory factory (spec §7 fixture rules)
# ----------------------------------------------------------------------------------------------


def message(role: str, content: Any) -> dict[str, Any]:
    """One ``agent_inner_thoughts.json`` message."""
    return {"role": role, "content": content}


def thoughts_payload(*, planner: Sequence[dict[str, Any]] | None = None, **agents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``{agent: [msg]}`` payload; ``planner_agent`` comes first so the loader's ordering is observable."""
    payload: dict[str, Any] = {}
    if planner is not None:
        payload["planner_agent"] = list(planner)
    for name, messages in agents.items():
        payload[name] = list(messages)
    return payload


def case(
    name: str,
    *,
    feature: str = "Feature: Checkout",
    failure: str | None = None,
    sysout: str | Sequence[str] | None = None,
    final_response: str | None = None,
    props: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """One synthesized JUnit testcase (``failure=None`` -> a passing testcase)."""
    out_lines: list[str] = []
    if final_response is not None:
        out_lines.append(f"Final Response: {final_response}")
    if isinstance(sysout, str):
        out_lines.extend(sysout.splitlines() or [""])
    elif sysout is not None:
        out_lines.extend(sysout)
    return {"name": name, "classname": feature, "failure": failure, "sysout": out_lines, "props": dict(props or {})}


def _property(name: str, value: str) -> str:
    return f"      <property name={quoteattr(name)} value={quoteattr(value)} />"


def junit_xml(cases: Sequence[Mapping[str, Any]], suite_properties: Mapping[str, str] | None = None) -> str:
    """Minimal JUnit tree with the property set written by ``utils/junit_helper.py`` (spec §2.1)."""
    failures = sum(1 for spec in cases if spec.get("failure") is not None)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<testsuite name="pytest" tests="{len(cases)}" errors="0" failures="{failures}" skipped="0" time="12.5">']
    if suite_properties:
        lines.append("    <properties>")
        lines.extend(_property(name, str(value)) for name, value in suite_properties.items())
        lines.append("    </properties>")
    for spec in cases:
        lines.append(f'  <testcase name={quoteattr(str(spec["name"]))} classname={quoteattr(str(spec.get("classname", "")))} time="1.5">')
        lines.append("    <properties>")
        for name, value in (spec.get("props") or {}).items():
            lines.append(_property(str(name), str(value)))
        lines.append("    </properties>")
        if spec.get("failure") is not None:
            lines.append(f"    <failure message={quoteattr(str(spec['failure']))}>Assertion with result: False</failure>")
        for text in spec.get("sysout") or []:
            lines.append(f"    <system-out>{escape(str(text))}</system-out>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def make_run_dir(
    root: Path,
    *,
    cases: Sequence[Mapping[str, Any]],
    feature_text: str | None = DEFAULT_FEATURE,
    feature_rel: str = DEFAULT_FEATURE_REL,
    thoughts: Any = None,
    thoughts_raw: str | None = None,
    thoughts_rel: str = DEFAULT_THOUGHTS_REL,
    screenshots: Sequence[str] = (),
    screenshots_rel: str = DEFAULT_SCREENSHOTS_REL,
    include_screenshot_props: bool = True,
    props: Mapping[str, str | None] | None = None,
    suite_properties: Mapping[str, str] | None = None,
    junit_rel: str = DEFAULT_JUNIT_REL,
) -> Path:
    """Write a synthesized Hercules run directory and return its root.

    Default path properties are absolute (the real Hercules writes absolute paths); ``props`` overrides
    or removes (``None``) them per run, which is how the unresolvable-path and missing-artifact cases are
    built. ``thoughts`` is dumped as JSON, ``thoughts_raw`` is written verbatim (empty/broken files).
    """
    root.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, str] = {}

    if feature_text is not None:
        feature_path = root / feature_rel
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        feature_path.write_text(feature_text, encoding="utf-8")
        defaults["Feature File"] = str(feature_path)

    if thoughts is not None or thoughts_raw is not None:
        thoughts_path = root / thoughts_rel
        thoughts_path.parent.mkdir(parents=True, exist_ok=True)
        thoughts_path.write_text(thoughts_raw if thoughts_raw is not None else json.dumps(thoughts, ensure_ascii=False, indent=2), encoding="utf-8")
        defaults["Planner Thoughts"] = str(thoughts_path)

    if include_screenshot_props:
        screenshots_dir = root / screenshots_rel
        if screenshots:
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            for name in screenshots:
                (screenshots_dir / name).write_bytes(b"")
        defaults[PROP_PROOFS_SCREENSHOT] = str(screenshots_dir)
        if screenshots_dir.parent.exists():
            defaults[PROP_PROOFS_BASE_FOLDER] = str(screenshots_dir.parent)
        defaults["Network Logs"] = str(root / "proofs/stake1/run1/network_logs.har")
        defaults["Output File"] = str(root / "output/checkout_20260920.xml")

    overrides = dict(props or {})
    resolved_cases: list[dict[str, Any]] = []
    for spec in cases:
        merged: dict[str, str] = dict(defaults)
        for key, value in (spec.get("props") or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = str(value)
        for key, value in overrides.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = str(value)
        resolved_cases.append({"name": spec["name"], "classname": spec.get("classname", ""), "failure": spec.get("failure"), "sysout": spec.get("sysout"), "props": dict(sorted(merged.items()))})

    junit_path = root / junit_rel
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path.write_text(junit_xml(resolved_cases, suite_properties), encoding="utf-8")
    return root


def load_cases(run_dir: Path, junit_path: str | None = None) -> list[TestCaseEvidence]:
    return load_bundle(str(run_dir), junit_path).cases


def load_first_case(run_dir: Path) -> TestCaseEvidence:
    return load_cases(run_dir)[0]


def bundle_of(run_dir: Path) -> EvidenceBundle:
    return load_bundle(str(run_dir))


def rule_outcome(run_dir: Path) -> RuleOutcome:
    """Rule-layer outcome of the single synthesized case (sugar for the signature tests)."""
    return apply_rules(load_first_case(run_dir))


# ----------------------------------------------------------------------------------------------
# FakeAnalyzer (spec §7: programmable values / exceptions / invalid-output sequences)
# ----------------------------------------------------------------------------------------------


class FakeAnalyzer:
    """Offline analyzer stand-in: returns the next programmed value and records every request."""

    def __init__(
        self,
        outputs: Sequence[Any] | None = None,
        *,
        error: BaseException | Sequence[BaseException] | None = None,
        builder: Callable[[LlmAnalysisRequest], Any] | None = None,
    ) -> None:
        self.outputs = list(outputs) if outputs is not None else []
        self.error = error
        self.builder = builder
        self.calls: list[LlmAnalysisRequest] = []
        self.instances: list[FakeAnalyzer] = []

    async def analyze(self, request: LlmAnalysisRequest) -> Any:
        self.calls.append(request)
        if self.error is not None:
            errors = list(self.error) if isinstance(self.error, Sequence) else [self.error]
            index = min(len(self.calls) - 1, len(errors) - 1)
            if index >= 0:
                raise errors[index]
        if self.builder is not None:
            return self.builder(request)
        if not self.outputs:
            return valid_output(category="inconclusive", confidence=0.2, summary="no evidence", evidence_refs=[])
        value = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, LlmAnalysisOutput):
            return value
        if isinstance(value, Mapping):
            return dict(value)
        return str(value)

    @property
    def last_request(self) -> LlmAnalysisRequest:
        return self.calls[-1]


def output(**kwargs: Any) -> LlmAnalysisOutput:
    """A valid-shaped LlmAnalysisOutput (fields can be deliberately invalid, e.g. ``category="bug"``)."""
    kwargs.setdefault("category", "test_rot")
    kwargs.setdefault("confidence", 0.7)
    kwargs.setdefault("summary", "element gone after redirect")
    kwargs.setdefault("suggestion", "")
    kwargs.setdefault("evidence_refs", [])
    return LlmAnalysisOutput(**kwargs)


valid_output = output


def refs_of_kind(request: LlmAnalysisRequest, kinds: Sequence[str] = TEXT_KINDS) -> list[str]:
    return [item.ref_id for item in request.seed_evidence if item.kind in kinds]


def text_ref(request: LlmAnalysisRequest, position: int = 0) -> str:
    """Ref id of the ``position``-th text evidence item of the package (never a screenshot)."""
    refs = refs_of_kind(request)
    return refs[position]


def screenshot_ref(request: LlmAnalysisRequest, position: int = 0) -> str:
    refs = [item.ref_id for item in request.seed_evidence if item.kind == "screenshot"]
    return refs[position]


def items_by_ref(request: LlmAnalysisRequest) -> dict[str, Any]:
    return {item.ref_id: item for item in request.seed_evidence}


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture()
def sample_run_dir() -> Path:
    return SAMPLE_RUN_DIR


@pytest.fixture()
def run_dir_factory(tmp_path: Path) -> Callable[..., Path]:
    """``make_run_dir`` bound to the test's tmp dir (unique sub-directory per call)."""
    counter = {"value": 0}

    def factory(**kwargs: Any) -> Path:
        counter["value"] += 1
        return make_run_dir(tmp_path / f"run_{counter['value']}", **kwargs)

    return factory
