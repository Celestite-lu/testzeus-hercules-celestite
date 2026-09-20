"""Group A — parser, exit codes and logger wiring (spec §8 cases 1-3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from record2gherkin import cli
from tests.record2gherkin.cli.conftest import REPO_ROOT


def test_help_lists_four_subcommands(capsys) -> None:
    """Case 1: ``--help`` exits 0 and lists the four sub-commands."""
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for name in ("record", "generate", "run", "analyze"):
        assert name in out


def test_module_entry_point_help() -> None:
    """Case 1b: ``python -m record2gherkin --help`` works through ``__main__.py`` (spec §0/§9.2)."""
    completed = subprocess.run([sys.executable, "-m", "record2gherkin", "--help"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0
    for name in ("record", "generate", "run", "analyze"):
        assert name in completed.stdout


def test_missing_subcommand_exit_2(capsys) -> None:
    """Case 2: no sub-command is an argparse usage error and exits 2."""
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_configure_logger_called(monkeypatch) -> None:
    """Case 3: ``main()`` configures the product logger before dispatching anything."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "configure_logger", lambda level="INFO": calls.append(level))

    assert cli.main(["record", "--manual"]) == 0
    assert calls == ["INFO"]


def test_record_without_url_or_manual_exit_2(caplog) -> None:
    """Spec §7: a missing target URL is a usage error, not a browser launch."""
    assert cli.main(["record"]) == 2
    assert "record 需要目标 url" in caplog.text


def test_unknown_subcommand_exit_2(capsys) -> None:
    """Spec §1.2: argparse's own errors already exit with 2."""
    assert cli.main(["frobnicate"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_unexpected_exception_exit_2(monkeypatch, caplog) -> None:
    """Spec §7 fallback: an unexpected error logs its type and exits 2 instead of dumping a traceback."""

    def _broken() -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "_log_manual_instructions", _broken)

    assert cli.main(["record", "--manual"]) == cli.EXIT_USAGE
    assert "unexpected RuntimeError [kaboom]" in caplog.text


def test_paths_are_repo_anchored() -> None:
    """Spec §2.3/§5.2: default paths hang off the repository root, not the current directory."""
    assert Path(cli.BOOKMARKLET_PATH).is_file()
    assert Path(cli.RECORDER_SOURCE_PATH).is_file()
    assert cli.RECORDINGS_DIR == REPO_ROOT / "dev_runs" / "recordings"
    assert cli.RUNS_DIR == REPO_ROOT / "dev_runs" / "cli_runs"
