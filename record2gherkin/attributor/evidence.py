"""Evidence loading for the failure attributor (spec §2).

A Hercules run directory is turned into an :class:`EvidenceBundle`: the JUnit XML is the entry
point (every testcase carries all artifact paths as properties, ``utils/junit_helper.py:108-146``)
and every referenced artifact is resolved on disk with a two-step strategy (as-is path, then a
``run_dir/**/<filename>`` glob).

Tolerance rule (spec §2.1): a missing/unparseable JUnit raises :class:`AttributionError`; everything
else degrades to a ``None`` field plus a ``missing`` marker so attribution can still run on partial
artifacts. This module is part of the model-free layer: it never imports an LLM/network module.

The shared vocabulary (:data:`Category`, :data:`Route`, :class:`EvidenceEntry`, :class:`AttributionError`,
the §6.3 evidence locator grammar) lives here so that ``rules.py`` / ``llm_layer.py`` / ``report.py``
can all depend on it without an import cycle.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from xml.etree import ElementTree as ET

from testzeus_hercules.utils.logger import logger

# ----------------------------------------------------------------------------------------------
# Shared vocabulary (spec §1.1, §5, §6.3)
# ----------------------------------------------------------------------------------------------

Category = Literal["product_bug", "test_rot", "environment", "agent_limit", "inconclusive"]

#: All five categories, in spec order (used for validation and deterministic iteration).
CATEGORIES: tuple[Category, ...] = ("product_bug", "test_rot", "environment", "agent_limit", "inconclusive")

#: Rule-layer routing value; ``needs_llm`` never reaches a final result (spec §1.1, §3.4).
Route = Literal["product_bug", "test_rot", "environment", "agent_limit", "inconclusive", "needs_llm"]

#: Evidence kinds (spec §4.1 / §6.1).
KIND_JUNIT_FAILURE = "junit_failure"
KIND_JUNIT_SYSOUT = "junit_sysout"
KIND_JUNIT_PROPERTY = "junit_property"
KIND_THOUGHTS = "thoughts"
KIND_SCREENSHOT = "screenshot"
KIND_FEATURE_FILE = "feature_file"
TEXT_KINDS: tuple[str, ...] = (KIND_JUNIT_FAILURE, KIND_JUNIT_SYSOUT, KIND_JUNIT_PROPERTY, KIND_THOUGHTS, KIND_FEATURE_FILE)

#: Evidence locator prefixes (spec §6.3) — the only grammar the back-checker understands.
REF_JUNIT_FAILURE = "junit_failure"
REF_SYSOUT = "sysout"
REF_SYSOUT_FINAL_RESPONSE = "sysout:final_response"
REF_PROPERTY_PREFIX = "property:"
REF_THOUGHTS_PREFIX = "thoughts:"
REF_SCREENSHOT_PREFIX = "screenshot:"
REF_FEATURE_PREFIX = "feature:"


class AttributionError(Exception):
    """Unrecoverable attributor error: JUnit missing/unparseable, analyzer not configurable (spec §5)."""


@dataclass
class EvidenceEntry:
    """One citable piece of evidence: ``kind`` + §6.3 locator + raw-text excerpt (spec §6.1)."""

    kind: str
    reference: str
    excerpt: str


# ----------------------------------------------------------------------------------------------
# Evidence structures (spec §1.2)
# ----------------------------------------------------------------------------------------------

#: JUnit property keys written by ``utils/junit_helper.py:111-124`` (upstream typos preserved).
PROP_TERMINATE = "Terminate"
PROP_FEATURE_FILE = "Feature File"
PROP_OUTPUT_FILE = "Output File"
PROP_PROOFS_VIDEO = "Proofs Video"
PROP_PROOFS_SCREENSHOT = "Proofs Screenshot"
PROP_NETWORK_LOGS = "Network Logs"
PROP_AGENTS_INTERNAL_LOGS = "Agents Internal Logs"
PROP_PLANNER_THOUGHTS = "Planner Thoughts"

#: The full upstream key carries a typo ("netwrok"); match by prefix only (spec §2.1, plan §8).
PROP_PROOFS_BASE_FOLDER_PREFIX = "Proofs Base Folder"

#: ``missing`` markers used by :attr:`TestCaseEvidence.missing` (spec §2.2).
MISSING_THOUGHTS = "thoughts"
MISSING_SCREENSHOTS = "screenshots_dir"
MISSING_FEATURE_FILE = "feature_file"

#: ``{tool}_{phase}_{ns}.png`` — the shape produced by ``playwright_manager.take_screenshots`` (spec §2.4).
SCREENSHOT_NAME_RE = re.compile(r"^(?P<tool>.+?)_(?P<phase>start|end)_(?P<ns>\d{10,})\.png$")

#: Literal ``*.png`` mentions inside planner text (v1 round<->screenshot anchoring, spec §2.4).
SCREENSHOT_MENTION_RE = re.compile(r"[\w/.\-]+\.png")

FINAL_RESPONSE_PREFIX = "Final Response: "

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace and strip — the comparison form used by the evidence back-check (spec §4.4)."""
    return _WHITESPACE_RE.sub(" ", text).strip()


@dataclass
class ScreenshotRef:
    """One screenshot file, parsed from its name (spec §1.2/§2.4)."""

    path: str
    filename: str
    tool: str
    phase: str
    ts_ns: int | None
    is_final_state: bool = False


@dataclass
class ThoughtsRound:
    """One planner chat message (spec §1.2/§2.3); ``index`` is the flattened 0-based round id."""

    index: int
    role: str
    content_text: str
    content_json: object | None = None


@dataclass
class TestCaseEvidence:
    """Everything the attributor knows about one JUnit testcase (spec §1.2)."""

    scenario: str
    feature_name: str
    failure_message: str | None
    final_response: str | None
    system_out: str | None
    properties: dict[str, str] = field(default_factory=dict)
    terminate: str = "unknown"
    feature_text: str | None = None
    feature_path: str | None = None
    thoughts_path: str | None = None
    thoughts: list[ThoughtsRound] = field(default_factory=list)
    screenshots: list[ScreenshotRef] = field(default_factory=list)
    network_log_path: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def is_failure(self) -> bool:
        """``True`` when the testcase carries a ``<failure>`` node (spec §2.1).

        A ``<failure>`` node without a ``message`` attribute loads as ``failure_message == ""`` and
        still counts as a failure; ``None`` means "no failure node at all".
        """
        return self.failure_message is not None


@dataclass
class EvidenceBundle:
    """One JUnit file parsed into testcase evidence (spec §1.2)."""

    junit_path: str
    cases: list[TestCaseEvidence] = field(default_factory=list)
    suite_properties: dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------
# JUnit parsing (spec §2.1)
# ----------------------------------------------------------------------------------------------


def find_latest_junit(run_dir: str) -> str | None:
    """Newest ``run_dir/**/*.xml`` by (mtime, path); ``None`` when there is none (spec §5)."""
    candidates = sorted(glob.glob(os.path.join(run_dir, "**", "*.xml"), recursive=True))
    if not candidates:
        return None
    return max(candidates, key=lambda path: (os.path.getmtime(path), path))


def load_bundle(run_dir: str, junit_path: str | None = None) -> EvidenceBundle:
    """Load a run directory into an :class:`EvidenceBundle` (spec §2).

    ``junit_path=None`` picks the newest XML below ``run_dir``; a missing or unparseable JUnit raises
    :class:`AttributionError` (the only load-time hard failure, spec §2.1).
    """
    path = junit_path or find_latest_junit(run_dir)
    if path is None:
        raise AttributionError(f"no JUnit XML found under run dir {run_dir!r}")
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise AttributionError(f"cannot parse JUnit XML {path!r}: {exc}") from exc

    root = tree.getroot()
    if root.tag == "testcase":
        suites: list[ET.Element] = []
        testcases = [root]
    else:
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        testcases = [case for suite in suites for case in suite.findall("testcase")]

    suite_properties: dict[str, str] = {}
    for suite in suites:
        suite_properties.update(_properties_of(suite))

    bundle = EvidenceBundle(junit_path=path, suite_properties=suite_properties)
    for testcase in testcases:
        bundle.cases.append(_load_case(run_dir, testcase))
    logger.debug("attributor: loaded %s testcase(s) from %s", len(bundle.cases), path)
    return bundle


def _properties_of(element: ET.Element) -> dict[str, str]:
    """``{name: value}`` of every direct ``<property>`` child (name kept verbatim, typos included)."""
    properties: dict[str, str] = {}
    for prop in element.findall("./properties/property"):
        name = prop.get("name")
        if name is not None:
            properties[name] = prop.get("value", "")
    return properties


def _failure_message(testcase: ET.Element) -> str | None:
    """``<failure message=...>`` text, or ``None`` when the testcase has no ``<failure>`` node (spec §2.1)."""
    failure = testcase.find("failure")
    if failure is None:
        return None
    message = failure.get("message")
    if message is None:
        message = (failure.text or "").strip()
    return message


def _system_out(testcase: ET.Element) -> str | None:
    """Concatenated ``<system-out>`` texts (Hercules writes one node per output line group, spec §2.1)."""
    parts = [node.text or "" for node in testcase.findall("system-out")]
    text = "\n".join(parts)
    return text if text.strip() else None


def _final_response(system_out: str | None) -> str | None:
    """First ``Final Response: `` line of system-out with the prefix stripped (spec §2.1)."""
    if not system_out:
        return None
    for line in system_out.splitlines():
        if line.startswith(FINAL_RESPONSE_PREFIX):
            return line[len(FINAL_RESPONSE_PREFIX) :]
    return None


# ----------------------------------------------------------------------------------------------
# Path resolution (spec §2.2)
# ----------------------------------------------------------------------------------------------


def _resolve_path(run_dir: str, value: str | None) -> str | None:
    """Two-step resolution: the value as-is, else the first sorted ``run_dir/**/<basename>`` (spec §2.2)."""
    if not value:
        return None
    if os.path.exists(value):
        return value
    basename = os.path.basename(value.rstrip("/")) or value
    matches = sorted(glob.glob(os.path.join(run_dir, "**", basename), recursive=True))
    return matches[0] if matches else None


def _first_property_with_prefix(properties: Mapping[str, str], prefix: str) -> str | None:
    """Value of the first property whose name starts with ``prefix`` (spec §2.1; upstream typos tolerated)."""
    for name, value in properties.items():
        if name.startswith(prefix):
            return value
    return None


# ----------------------------------------------------------------------------------------------
# inner_thoughts (spec §2.3, facts from core/runner.py:91-135)
# ----------------------------------------------------------------------------------------------

PLANNER_AGENT_KEY = "planner_agent"


def _normalize_content(content: Any) -> tuple[str, object | None]:
    """content -> (text, parsed json) per spec §2.3: dict/list are dumped as JSON, ``None`` becomes ``""``."""
    if content is None:
        return "", None
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False), content
    if isinstance(content, str):
        return content, None
    return str(content), None


def _load_thoughts(path: str) -> tuple[list[ThoughtsRound], bool]:
    """Flatten ``{agent: [msg]}`` into rounds; ``(rounds, ok)`` with ``ok=False`` for empty/non-dict/unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("attributor: cannot read inner thoughts %s (%s)", path, exc)
        return [], False
    if not isinstance(payload, Mapping):
        return [], False

    ordered: list[tuple[str, Any]] = []
    if PLANNER_AGENT_KEY in payload:
        ordered.append((PLANNER_AGENT_KEY, payload[PLANNER_AGENT_KEY]))
    ordered.extend((str(key), value) for key, value in payload.items() if key != PLANNER_AGENT_KEY)

    rounds: list[ThoughtsRound] = []
    for agent_name, messages in ordered:
        if not isinstance(messages, list):
            logger.debug("attributor: skipping non-list thoughts section %r", agent_name)
            continue
        for message in messages:
            if isinstance(message, Mapping):
                role = str(message.get("role", "unknown"))
                text, parsed = _normalize_content(message.get("content"))
            else:
                role = "unknown"
                text, parsed = _normalize_content(message)
            rounds.append(ThoughtsRound(index=len(rounds), role=role, content_text=text, content_json=parsed))
    return rounds, bool(rounds)


# ----------------------------------------------------------------------------------------------
# Screenshots (spec §2.4)
# ----------------------------------------------------------------------------------------------


def _parse_screenshot_name(path: str) -> ScreenshotRef:
    filename = os.path.basename(path)
    match = SCREENSHOT_NAME_RE.match(filename)
    if match is None:
        return ScreenshotRef(path=path, filename=filename, tool=os.path.splitext(filename)[0], phase="other", ts_ns=None)
    return ScreenshotRef(
        path=path,
        filename=filename,
        tool=match.group("tool"),
        phase=match.group("phase"),
        ts_ns=int(match.group("ns")),
    )


def _screenshot_search(run_dir: str, properties: Mapping[str, str]) -> tuple[str, str] | None:
    """``(root, pattern)`` of the screenshot search: dedicated dir, else base folder fallback (spec §2.4)."""
    screenshot_dir = _resolve_path(run_dir, properties.get(PROP_PROOFS_SCREENSHOT))
    if screenshot_dir is not None and os.path.isdir(screenshot_dir):
        return screenshot_dir, os.path.join(screenshot_dir, "*.png")
    base_folder = _resolve_path(run_dir, _first_property_with_prefix(properties, PROP_PROOFS_BASE_FOLDER_PREFIX))
    if base_folder is not None and os.path.isdir(base_folder):
        return base_folder, os.path.join(base_folder, "**", "*.png")
    return None


def _collect_screenshots(root: str, pattern: str) -> list[ScreenshotRef]:
    """Sorted screenshot list: by ``(has ts, ts, filename)`` (spec §2.4)."""
    refs = [_parse_screenshot_name(path) for path in glob.glob(pattern, recursive=True)]
    refs.sort(key=lambda ref: (0 if ref.ts_ns is not None else 1, ref.ts_ns or 0, ref.filename))
    _mark_final_state(refs)
    return refs


def _mark_final_state(refs: list[ScreenshotRef]) -> None:
    """Last ``_end`` (max ts) plus its closest preceding ``_start``; last item when no ``_end`` (spec §2.4)."""
    if not refs:
        return
    end_positions = [position for position, ref in enumerate(refs) if ref.phase == "end"]
    if not end_positions:
        refs[-1].is_final_state = True
        return
    last_end = end_positions[-1]
    refs[last_end].is_final_state = True
    for position in range(last_end - 1, -1, -1):
        if refs[position].phase == "start":
            refs[position].is_final_state = True
            break


def final_state_screenshots(evidence: TestCaseEvidence) -> list[ScreenshotRef]:
    """The failure-moment context pair/window (spec §2.4)."""
    return [ref for ref in evidence.screenshots if ref.is_final_state]


def find_screenshot_anchors(evidence: TestCaseEvidence) -> dict[int, list[str]]:
    """Literal round->screenshot anchors: round index -> mentioned screenshot filenames (spec §2.4).

    ``best-effort`` and purely presentational (LLM package / report appendix); rounds without a literal
    ``*.png`` mention are simply absent from the mapping.
    """
    known = {ref.filename for ref in evidence.screenshots}
    anchors: dict[int, list[str]] = {}
    for round_ in evidence.thoughts:
        mentioned: list[str] = []
        for mention in SCREENSHOT_MENTION_RE.findall(round_.content_text):
            basename = os.path.basename(mention)
            if basename in known and basename not in mentioned:
                mentioned.append(basename)
        if mentioned:
            anchors[round_.index] = mentioned
    return anchors


# ----------------------------------------------------------------------------------------------
# Evidence locators (spec §6.3) — used by the LLM back-checker
# ----------------------------------------------------------------------------------------------


def _as_int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def resolve_reference(evidence: TestCaseEvidence, reference: str) -> str | None:
    """Resolve a §6.3 locator to its referent text; ``None`` means unresolvable (out of range/unknown syntax)."""
    if reference == REF_JUNIT_FAILURE:
        return evidence.failure_message
    if reference == REF_SYSOUT:
        return evidence.system_out
    if reference == REF_SYSOUT_FINAL_RESPONSE:
        return evidence.final_response
    if reference.startswith(REF_PROPERTY_PREFIX):
        return evidence.properties.get(reference[len(REF_PROPERTY_PREFIX) :])
    if reference.startswith(REF_THOUGHTS_PREFIX):
        index = _as_int(reference[len(REF_THOUGHTS_PREFIX) :])
        if index is None or not 0 <= index < len(evidence.thoughts):
            return None
        return evidence.thoughts[index].content_text
    if reference.startswith(REF_FEATURE_PREFIX):
        line_number = _as_int(reference[len(REF_FEATURE_PREFIX) :])
        if line_number is None or evidence.feature_text is None:
            return None
        lines = evidence.feature_text.splitlines()
        if not 1 <= line_number <= len(lines):
            return None
        return lines[line_number - 1]
    if reference.startswith(REF_SCREENSHOT_PREFIX):
        filename = reference[len(REF_SCREENSHOT_PREFIX) :]
        return filename if any(ref.filename == filename for ref in evidence.screenshots) else None
    return None


def _load_case(run_dir: str, testcase: ET.Element) -> TestCaseEvidence:
    """Build one :class:`TestCaseEvidence` from a ``<testcase>`` element (spec §2.1-§2.5)."""
    properties = _properties_of(testcase)
    system_out = _system_out(testcase)
    missing: list[str] = []

    thoughts_path = _resolve_path(run_dir, properties.get(PROP_PLANNER_THOUGHTS))
    thoughts: list[ThoughtsRound] = []
    if thoughts_path is None:
        missing.append(MISSING_THOUGHTS)
    else:
        thoughts, ok = _load_thoughts(thoughts_path)
        if not ok:
            missing.append(MISSING_THOUGHTS)

    search = _screenshot_search(run_dir, properties)
    if search is None:
        screenshots: list[ScreenshotRef] = []
        missing.append(MISSING_SCREENSHOTS)
    else:
        screenshots = _collect_screenshots(*search)

    feature_path = _resolve_path(run_dir, properties.get(PROP_FEATURE_FILE))
    feature_text: str | None = None
    if feature_path is None:
        missing.append(MISSING_FEATURE_FILE)
    else:
        try:
            with open(feature_path, "r", encoding="utf-8", errors="replace") as handle:
                feature_text = handle.read()
        except OSError as exc:
            logger.warning("attributor: cannot read feature file %s (%s)", feature_path, exc)
            feature_path = None
            missing.append(MISSING_FEATURE_FILE)

    return TestCaseEvidence(
        scenario=testcase.get("name", ""),
        feature_name=testcase.get("classname", ""),
        failure_message=_failure_message(testcase),
        final_response=_final_response(system_out),
        system_out=system_out,
        properties=properties,
        terminate=properties.get(PROP_TERMINATE, "unknown"),
        feature_text=feature_text,
        feature_path=feature_path,
        thoughts_path=thoughts_path,
        thoughts=thoughts,
        screenshots=screenshots,
        # Path-only passthrough (spec §1.2/§9): the content is never parsed, so failures stay silent.
        network_log_path=_resolve_path(run_dir, properties.get(PROP_NETWORK_LOGS)),
        missing=missing,
    )
