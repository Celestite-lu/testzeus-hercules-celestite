"""Group C — ``generate``: two-stage distillation and the P0-2 placeholder filling (spec §8 cases 8-17)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from record2gherkin import cli
from record2gherkin.distiller import distill_events
from tests.record2gherkin.cli.conftest import MASKED_KEY, write_json


class _BoomPolisher:
    """Offline polisher stand-in whose call always fails (spec §8 case 15)."""

    async def polish(self, skeleton_text: str, events: Mapping | list) -> str:
        raise RuntimeError("boom")


class _TitlePolisher:
    """Offline polisher stand-in returning a grounded rewrite (spec §8 case 16)."""

    def __init__(self, marker: str = "Place a paid order") -> None:
        self.marker = marker

    async def polish(self, skeleton_text: str, events: Mapping | list) -> str:
        return skeleton_text.replace("Scenario: Checkout", f"Scenario: {self.marker}", 1)


def test_generate_basic_feature_written(events_file: Path) -> None:
    """Case 8: the default output path is the events file's sibling ``<stem>.feature``."""
    assert cli.main(["generate", str(events_file)]) == cli.EXIT_OK

    feature = events_file.with_suffix(".feature")
    assert feature.is_file()
    text = feature.read_text(encoding="utf-8")
    assert "Feature:" in text
    assert "Scenario:" in text


def test_generate_out_flag(tmp_path: Path, events_file: Path) -> None:
    """Case 9: ``--out`` wins and its parent directory is created on the fly."""
    target = tmp_path / "nested" / "deeper" / "custom.feature"

    assert cli.main(["generate", str(events_file), "--out", str(target)]) == cli.EXIT_OK

    assert target.is_file()
    assert not events_file.with_suffix(".feature").exists()


def test_generate_test_data_fills_placeholder(events_file: Path, tmp_path: Path, caplog) -> None:
    """Case 10: a provided value replaces the placeholder and the summary reports ``filled=1``."""
    test_data = write_json(tmp_path / "test-data.json", {MASKED_KEY: "s3cret"})

    assert cli.main(["generate", str(events_file), "--test-data", str(test_data)]) == cli.EXIT_OK

    text = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert "{{TEST_DATA" not in text
    assert "s3cret" in text
    assert "filled=1" in caplog.text


def test_generate_missing_test_data_warns(events_file: Path, tmp_path: Path, caplog) -> None:
    """Case 11: an unmatched key set warns per key (missing + unused) and keeps the placeholder."""
    test_data = write_json(tmp_path / "test-data.json", {"password_9": "x"})

    assert cli.main(["generate", str(events_file), "--test-data", str(test_data)]) == cli.EXIT_OK

    text = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert f"{{{{TEST_DATA:{MASKED_KEY}}}}}" in text
    assert f"missing_test_data:{MASKED_KEY}" in caplog.text
    assert "unused_test_data:password_9" in caplog.text
    assert "filled=0" in caplog.text


def test_generate_no_test_data_flag_warns_needed_keys(events_file: Path, caplog) -> None:
    """Case 12: without ``--test-data`` every needed key is listed and the placeholder survives."""
    assert cli.main(["generate", str(events_file)]) == cli.EXIT_OK

    text = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert f"{{{{TEST_DATA:{MASKED_KEY}}}}}" in text
    assert f"missing_test_data:{MASKED_KEY}" in caplog.text
    assert "filled=0" in caplog.text


def test_generate_bad_test_data_shape_exit_2(events_file: Path, tmp_path: Path, caplog) -> None:
    """Case 13a: a non-object file and an object/array value are both usage errors."""
    array_file = write_json(tmp_path / "array.json", [MASKED_KEY])
    assert cli.main(["generate", str(events_file), "--test-data", str(array_file)]) == cli.EXIT_USAGE
    assert "must be a JSON object" in caplog.text
    caplog.clear()

    object_file = write_json(tmp_path / "object.json", {MASKED_KEY: {"nested": 1}})
    assert cli.main(["generate", str(events_file), "--test-data", str(object_file)]) == cli.EXIT_USAGE
    assert MASKED_KEY in caplog.text
    assert not events_file.with_suffix(".feature").exists()


def test_generate_unusable_test_data_values_are_treated_as_absent(events_file: Path, tmp_path: Path, caplog) -> None:
    """Case 13b (R3): ``true``/blank values count as not provided, so no ``filled`` is ever虚报."""
    for index, value in enumerate([True, "   ", None]):
        test_data = write_json(tmp_path / f"test-data-{index}.json", {MASKED_KEY: value})

        assert cli.main(["generate", str(events_file), "--test-data", str(test_data)]) == cli.EXIT_OK

        text = events_file.with_suffix(".feature").read_text(encoding="utf-8")
        assert f"{{{{TEST_DATA:{MASKED_KEY}}}}}" in text
        assert "filled=0" in caplog.text
        assert f"missing_test_data:{MASKED_KEY}" in caplog.text
        assert "unused_test_data" not in caplog.text
        caplog.clear()


def test_generate_skeleton_self_check_failure_exit_1(events_file: Path, monkeypatch, caplog) -> None:
    """Spec §1.2/§3.1: a ``DistillError`` (skeleton bug) is the only generate path that exits 1."""
    from record2gherkin.distiller import DistillError

    def _broken(*args, **kwargs):
        raise DistillError("skeleton self-check failed on valid input (bug): syntax_invalid")

    monkeypatch.setattr(cli, "distill_events", _broken)

    assert cli.main(["generate", str(events_file)]) == cli.EXIT_FAILED

    assert "skeleton self-check failed" in caplog.text
    assert not events_file.with_suffix(".feature").exists()


def test_generate_bad_events_exit_2(tmp_path: Path, caplog) -> None:
    """Case 14: a missing events file and an unparseable one are both usage errors."""
    assert cli.main(["generate", str(tmp_path / "absent.json")]) == cli.EXIT_USAGE

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert cli.main(["generate", str(broken)]) == cli.EXIT_USAGE

    assert "cannot read events JSON" in caplog.text


def test_generate_polish_factory_seam_fallback(events_file: Path, events_payload: dict[str, Any], monkeypatch, caplog) -> None:
    """Case 15: a failing polisher degrades to the skeleton, exit 0, ``fallback_reason`` in the log."""
    monkeypatch.setattr(cli, "_make_polisher", _BoomPolisher)

    assert cli.main(["generate", str(events_file), "--polish"]) == cli.EXIT_OK

    feature = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert feature == distill_events(events_payload).skeleton_text
    assert "fallback_reason=polish_error:" in caplog.text
    assert "used_llm_polish=False" in caplog.text


def test_generate_polish_factory_seam_adopted(events_file: Path, monkeypatch, caplog) -> None:
    """Case 16: an accepted polished draft becomes the feature and ``used_llm_polish=True``."""
    monkeypatch.setattr(cli, "_make_polisher", _TitlePolisher)

    assert cli.main(["generate", str(events_file), "--polish"]) == cli.EXIT_OK

    feature = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert "Scenario: Place a paid order" in feature
    assert "used_llm_polish=True" in caplog.text
    assert "fallback_reason=None" in caplog.text


def test_generate_only_needed_keys_reach_the_final_distillation(events_file: Path, tmp_path: Path, caplog) -> None:
    """Spec §3.1 step 6: ``test_data_values`` is needed ∩ provided — unused keys cannot leak into the run."""
    test_data = write_json(tmp_path / "test-data.json", {MASKED_KEY: "s3cret", "password_99": "unused-value"})

    assert cli.main(["generate", str(events_file), "--test-data", str(test_data)]) == cli.EXIT_OK

    feature = events_file.with_suffix(".feature").read_text(encoding="utf-8")
    assert "s3cret" in feature
    assert "unused-value" not in feature
    assert "unused_test_data:password_99" in caplog.text
    assert "filled=1" in caplog.text


def test_generate_deterministic(events_file: Path) -> None:
    """Case 17: the no-polish path is byte-deterministic across runs."""
    assert cli.main(["generate", str(events_file)]) == cli.EXIT_OK
    first = events_file.with_suffix(".feature").read_bytes()

    assert cli.main(["generate", str(events_file)]) == cli.EXIT_OK

    assert events_file.with_suffix(".feature").read_bytes() == first
