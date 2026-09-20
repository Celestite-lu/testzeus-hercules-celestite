"""Group D — ``run``: plan building, exit-code mapping, summary and key masking (spec §8 cases 18-25).

``run_feature`` is always monkeypatched: no Hercules sub-process, no browser, no LLM, no real key.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest
from record2gherkin import cli
from record2gherkin.evaluation.runner import (
    STATUS_FAILED,
    STATUS_NO_JUNIT,
    STATUS_PASSED,
    STATUS_TIMEOUT,
)
from tests.record2gherkin.cli.conftest import (
    FAKE_KEY,
    MIN_FEATURE_TEXT,
    make_run_result,
)


@pytest.fixture()
def feature_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo.feature"
    path.write_text(MIN_FEATURE_TEXT, encoding="utf-8")
    return path


def test_run_dry_run_exit_0(tmp_path: Path, feature_file: Path, key_file: Path, caplog) -> None:
    """Case 18: ``--dry-run`` prints the command and the project root, and prepares ``opt/input+output``."""
    out_dir = tmp_path / "run-out"

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file), "--dry-run"]) == cli.EXIT_OK

    text = caplog.text
    assert "python" in text
    assert "--input-file" in text
    assert str((out_dir / "opt").resolve()) in text
    assert (out_dir / "opt" / "input").is_dir()
    assert (out_dir / "opt" / "output").is_dir()


def test_run_dry_run_without_key_file(tmp_path: Path, feature_file: Path, missing_key_file: Path, caplog) -> None:
    """Case 19: a missing key file is tolerated by ``--dry-run`` (placeholder key, redacted env line)."""
    out_dir = tmp_path / "run-out"

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(missing_key_file), "--dry-run"]) == cli.EXIT_OK

    text = caplog.text
    assert "cannot read LLM key" in text
    assert "LLM_MODEL_API_KEY=***REDACTED***" in text
    # the stand-in key is a placeholder, not a secret: it must not leak into the log either
    assert cli.DRY_RUN_PLACEHOLDER_KEY not in text
    assert any(record.levelno == logging.WARNING and "cannot read LLM key" in record.getMessage() for record in caplog.records)


def test_run_dry_run_key_masked(tmp_path: Path, feature_file: Path, key_file: Path, caplog) -> None:
    """Case 20: a real (fake) key never appears in the dry-run log."""
    out_dir = tmp_path / "run-out"

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file), "--dry-run"]) == cli.EXIT_OK

    assert FAKE_KEY not in caplog.text
    assert "LLM_MODEL_API_KEY=***REDACTED***" in caplog.text


def test_run_missing_feature_exit_2(tmp_path: Path, key_file: Path, caplog) -> None:
    """Case 21: a missing feature file is a usage error on both the dry-run and the real path."""
    absent = tmp_path / "absent.feature"

    assert cli.main(["run", str(absent), "--out-dir", str(tmp_path / "dry"), "--key-file", str(key_file), "--dry-run"]) == cli.EXIT_USAGE
    assert cli.main(["run", str(absent), "--out-dir", str(tmp_path / "real"), "--key-file", str(key_file)]) == cli.EXIT_USAGE

    assert "feature file not found" in caplog.text
    assert not (tmp_path / "real" / "opt").exists()


def test_run_missing_key_file_exit_2(tmp_path: Path, feature_file: Path, missing_key_file: Path, caplog) -> None:
    """Case 22: without a key nothing is executed and no run directory is created."""
    out_dir = tmp_path / "run-out"

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(missing_key_file)]) == cli.EXIT_USAGE

    assert "cannot read LLM key" in caplog.text
    assert not (out_dir / "opt").exists()


def test_run_empty_key_file_exit_2(tmp_path: Path, feature_file: Path) -> None:
    """Spec §7: an empty key file cannot start a run either."""
    empty_key = tmp_path / "empty-key.txt"
    empty_key.write_text("\n", encoding="utf-8")

    assert cli.main(["run", str(feature_file), "--out-dir", str(tmp_path / "run-out"), "--key-file", str(empty_key)]) == cli.EXIT_USAGE
    assert not (tmp_path / "run-out" / "opt").exists()


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(STATUS_PASSED, 0), (STATUS_FAILED, 1), (STATUS_TIMEOUT, 3), (STATUS_NO_JUNIT, 4)],
)
def test_run_status_exit_codes(status: str, expected_code: int, tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Case 23: the four runner statuses map to 0/1/3/4 and the JUnit message is masked by the CLI (R2)."""
    out_dir = tmp_path / "run-out"
    message = f"engine echoed the key {FAKE_KEY} in the failure text"
    monkeypatch.setattr(cli, "run_feature", lambda *args, **kwargs: make_run_result(status, failure_message=message, run_dir=str(out_dir)))

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file)]) == expected_code

    text = caplog.text
    assert FAKE_KEY not in text
    if status in (STATUS_FAILED, STATUS_NO_JUNIT):
        assert "engine echoed the key" in text
        assert "***REDACTED***" in text


def test_run_summary_lists_artifacts(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Case 24: junit / same-stem HTML / proofs / log_files are listed when they exist."""
    out_dir = tmp_path / "run-out"
    junit = out_dir / "opt" / "output" / "demo.feature_result.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.write_text("<testsuite/>", encoding="utf-8")
    html = junit.with_suffix(".html")
    html.write_text("<html/>", encoding="utf-8")
    proofs = out_dir / "opt" / "proofs"
    proofs.mkdir(parents=True, exist_ok=True)
    log_files = out_dir / "opt" / "log_files"
    log_files.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "run_feature", lambda *args, **kwargs: make_run_result(STATUS_PASSED, junit_xml=str(junit), run_dir=str(out_dir)))

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file)]) == cli.EXIT_OK

    text = caplog.text
    for path in (junit, html, proofs, log_files):
        assert str(path.resolve()) in text


def test_run_summary_skips_missing_artifacts(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Case 24b: absent artefacts leave no line behind (no dangling paths in the summary)."""
    out_dir = tmp_path / "run-out"
    junit = out_dir / "opt" / "output" / "demo.feature_result.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.write_text("<testsuite/>", encoding="utf-8")
    monkeypatch.setattr(cli, "run_feature", lambda *args, **kwargs: make_run_result(STATUS_PASSED, junit_xml=str(junit), run_dir=str(out_dir)))

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file)]) == cli.EXIT_OK

    text = caplog.text
    assert "html_report" not in text
    assert "proofs" not in text
    assert "log_files" not in text


def test_run_summary_prints_none_metrics(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Spec §5.3: absent metrics (``None``) print as ``-`` instead of ``None``."""
    out_dir = tmp_path / "run-out"
    result = make_run_result(STATUS_TIMEOUT, duration_s=None, cost_usd=None, total_tokens=None, run_dir=str(out_dir))
    monkeypatch.setattr(cli, "run_feature", lambda *args, **kwargs: result)

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file)]) == cli.EXIT_ATTRIBUTION

    assert "run: status=timeout passed=False duration_s=- cost_usd=- total_tokens=-" in caplog.text


def test_run_next_step_hint(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Case 25: the summary points at the ``analyze`` command with this run's directory."""
    out_dir = tmp_path / "run-out"
    monkeypatch.setattr(cli, "run_feature", lambda *args, **kwargs: make_run_result(STATUS_PASSED, run_dir=str(out_dir)))

    assert cli.main(["run", str(feature_file), "--out-dir", str(out_dir), "--key-file", str(key_file)]) == cli.EXIT_OK

    assert f"analyze {out_dir}" in caplog.text


def test_run_keyboard_interrupt_exit_130(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Spec §5.5/§7: interrupting a run warns about the surviving child and exits 130."""

    def _interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_feature", _interrupted)

    assert cli.main(["run", str(feature_file), "--out-dir", str(tmp_path / "run-out"), "--key-file", str(key_file)]) == cli.EXIT_INTERRUPTED

    assert "子进程可能仍在后台运行" in caplog.text


def test_run_default_out_dir_is_repo_anchored(tmp_path: Path, feature_file: Path, key_file: Path, monkeypatch, caplog) -> None:
    """Spec §5.2: without ``--out-dir`` the run lands in ``<RUNS_DIR>/<run_id>`` (``RUNS_DIR`` redirected)."""
    runs_dir = tmp_path / "cli_runs"
    monkeypatch.setattr(cli, "RUNS_DIR", runs_dir)

    assert cli.main(["run", str(feature_file), "--key-file", str(key_file), "--dry-run"]) == cli.EXIT_OK

    run_id_lines = [record.getMessage() for record in caplog.records if "dry-run run_id=" in record.getMessage()]
    assert run_id_lines, caplog.text
    run_id = run_id_lines[0].split("dry-run run_id=", 1)[1]
    assert run_id.startswith("cli_")
    assert run_id.endswith(f"_{feature_file.stem}")
    assert (runs_dir / run_id / "opt" / "input").is_dir()
    assert (runs_dir / run_id / "opt" / "output").is_dir()


def test_make_run_id_appends_suffix_on_collision(tmp_path: Path, monkeypatch) -> None:
    """Spec §5.2: an existing run directory is never reused — ``_1``/``_2``… are appended."""

    class _FrozenDatetime:
        @staticmethod
        def now():
            return datetime(2026, 9, 20, 12, 0, 0)

    monkeypatch.setattr(cli, "datetime", _FrozenDatetime)
    feature = tmp_path / "demo.feature"
    base = tmp_path / "cli_runs"
    base.mkdir()

    first = cli._make_run_id(feature, base)
    assert first == "cli_20260920-120000_demo"
    (base / first).mkdir()
    assert cli._make_run_id(feature, base) == f"{first}_1"
    (base / f"{first}_1").mkdir()
    assert cli._make_run_id(feature, base) == f"{first}_2"
