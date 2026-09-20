"""Group E — ``analyze``: rules-only attribution over synthetic run directories (spec §8 cases 26-32)."""

from __future__ import annotations

import json
from pathlib import Path

from record2gherkin import cli
from record2gherkin.attributor import AttributionError
from tests.record2gherkin.cli.conftest import AGENT_LIMIT_MESSAGE, junit_xml


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_analyze_synthetic_rules_only(synth_run_dir_factory, caplog) -> None:
    """Case 26: the S1 failure message is attributed to ``agent_limit`` by the rule layer."""
    run_dir = synth_run_dir_factory()

    assert cli.main(["analyze", str(run_dir)]) == cli.EXIT_OK

    case_json = run_dir / "analysis" / "case-1.json"
    case_md = run_dir / "analysis" / "case-1.md"
    assert case_json.is_file()
    assert case_md.is_file()
    assert "失败归因报告" in case_md.read_text(encoding="utf-8")
    payload = _read_json(case_json)
    assert payload["category"] == "agent_limit"
    assert payload["decided_by"] == "rule"


def test_analyze_no_failures_exit_0(synth_run_dir_factory, caplog) -> None:
    """Case 27: a fully green JUnit produces no report directory and still exits 0."""
    run_dir = synth_run_dir_factory(failing=False)

    assert cli.main(["analyze", str(run_dir)]) == cli.EXIT_OK

    assert not (run_dir / "analysis").exists()
    assert "no failing testcases" in caplog.text


def test_analyze_missing_run_dir_exit_2(tmp_path: Path, caplog) -> None:
    """Case 28: a non-existent run directory is a usage error."""
    assert cli.main(["analyze", str(tmp_path / "absent")]) == cli.EXIT_USAGE

    assert "run dir not found" in caplog.text


def test_analyze_no_junit_exit_3(tmp_path: Path, caplog) -> None:
    """Case 29: a run directory without any JUnit XML is an attribution error (exit 3)."""
    empty = tmp_path / "empty-run"
    empty.mkdir()

    assert cli.main(["analyze", str(empty)]) == cli.EXIT_ATTRIBUTION

    assert "no JUnit XML found" in caplog.text


def test_analyze_llm_flag_degrades_gracefully(synth_run_dir_factory, monkeypatch, caplog) -> None:
    """Case 30: an unconstructible LLM analyzer degrades to the rules and still writes the report."""
    run_dir = synth_run_dir_factory()

    def _unavailable():
        raise AttributionError("agents_llm_config has no 'attributor_analyze' key")

    monkeypatch.setattr(cli, "_make_analyzer", _unavailable)

    assert cli.main(["analyze", str(run_dir), "--llm"]) == cli.EXIT_OK

    text = caplog.text
    assert "llm analyzer unavailable" in text
    assert "降级纯规则" in text
    assert (run_dir / "analysis" / "case-1.json").is_file()
    assert _read_json(run_dir / "analysis" / "case-1.json")["category"] == "agent_limit"


def test_analyze_llm_flag_rule_decided_case_stays_offline(synth_run_dir_factory) -> None:
    """Spec §6.2: with the real analyzer seam and a rule-decided case no model is ever consulted."""
    run_dir = synth_run_dir_factory()

    assert cli.main(["analyze", str(run_dir), "--llm"]) == cli.EXIT_OK

    payload = _read_json(run_dir / "analysis" / "case-1.json")
    assert payload["category"] == "agent_limit"
    assert payload["decided_by"] == "rule"


def test_analyze_junit_flag_selects_file(tmp_path: Path) -> None:
    """Case 31: ``--junit`` picks that file instead of globbing the newest XML."""
    run_dir = tmp_path / "run"
    (run_dir / "proofs").mkdir(parents=True)
    (run_dir / "log_files").mkdir()
    explicit = run_dir / "explicit_case.xml"
    explicit.write_text(junit_xml([{"name": "test_explicit_case", "failure": AGENT_LIMIT_MESSAGE}]), encoding="utf-8")
    globbed = run_dir / "output" / "globbed_case.xml"
    globbed.parent.mkdir()
    globbed.write_text(junit_xml([{"name": "test_globbed_case", "failure": "Assertion with result: False"}]), encoding="utf-8")

    assert cli.main(["analyze", str(run_dir), "--junit", str(explicit)]) == cli.EXIT_OK

    assert _read_json(run_dir / "analysis" / "case-1.json")["scenario"] == "test_explicit_case"


def test_analyze_out_dir_override(synth_run_dir_factory, tmp_path: Path) -> None:
    """Spec §1.1: ``--out-dir`` moves the reports away from the default ``<run_dir>/analysis``."""
    run_dir = synth_run_dir_factory()
    target = tmp_path / "reports"

    assert cli.main(["analyze", str(run_dir), "--out-dir", str(target)]) == cli.EXIT_OK

    assert (target / "case-1.json").is_file()
    assert (target / "case-1.md").is_file()
    assert not (run_dir / "analysis").exists()


def test_analyze_summary_table_line(synth_run_dir_factory, caplog) -> None:
    """Case 32: the summary carries one ``case-<i> | …`` line per failing testcase."""
    run_dir = synth_run_dir_factory()

    assert cli.main(["analyze", str(run_dir)]) == cli.EXIT_OK

    messages = [record.getMessage() for record in caplog.records if record.name == "testzeus_hercules.utils.logger"]
    summary = [message for message in messages if message.startswith("case-1 |")]
    assert summary, messages
    assert "agent_limit" in summary[0]
    assert "conf=0.95" in summary[0]
    assert "by=rule" in summary[0]


def test_analyze_json_report_is_deterministic(synth_run_dir_factory) -> None:
    """Spec §6.2: two rules-only runs over the same input are byte-identical."""
    run_dir = synth_run_dir_factory()

    assert cli.main(["analyze", str(run_dir)]) == cli.EXIT_OK
    first = (run_dir / "analysis" / "case-1.json").read_bytes()

    assert cli.main(["analyze", str(run_dir)]) == cli.EXIT_OK

    assert (run_dir / "analysis" / "case-1.json").read_bytes() == first
