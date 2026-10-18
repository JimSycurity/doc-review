"""Exercise the public command with no network or credential access."""

import json
from pathlib import Path

import pytest

from docreview.cli import main
from docreview.documentation_decisions import as_object, read_json


def test_review_default_is_modern_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["review"]) == 0
    report = as_object(read_json(capsys.readouterr().out), "report")
    assert report["mode"] == "dry-run"
    assert as_object(report["model"], "model")["requested"] == "typesafe/jev-1.14"
    assert as_object(report["summary"], "summary")["pass"] == 0


def test_dry_run_never_reads_supplied_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["review", "--key-file", str(tmp_path / "missing")]) == 0
    assert as_object(read_json(capsys.readouterr().out), "report")["mode"] == "dry-run"


def test_live_requires_explicit_key_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["review", "--live"]) == 2
    assert "requires --key-file" in capsys.readouterr().err


def test_repository_review_returns_located_hard_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "broken.md").write_text("# Broken\n\nNo declaration.\n")
    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    assert main(["review", "--root", str(tmp_path), "docs/broken.md"]) == 1
    report = as_object(read_json(capsys.readouterr().out), "report")
    assert as_object(report["summary"], "summary")["hard_failures"] == 1


def test_prepared_docstrings_do_not_implicitly_review_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "functions.json"
    source.write_text(
        json.dumps(
            [
                {
                    "path": "src/worker.py",
                    "name": "restore",
                    "signature": "def restore() -> None",
                    "docstring": "",
                    "body": "restore_worker()",
                    "line_start": 1,
                    "line_end": 2,
                    "required": True,
                }
            ]
        )
    )
    assert main(["review", "--docstrings", str(source)]) == 0
    report = as_object(read_json(capsys.readouterr().out), "report")
    assert as_object(report["summary"], "summary")["documents"] == 1


def test_contributed_review_cannot_write_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["review", "--root", str(tmp_path), "--out", "report.json"]) == 2
    assert not (tmp_path / "report.json").exists()
    assert "contributed mode forbids" in capsys.readouterr().err


def test_explicit_model_comparison_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["review", "--model", "microsoft/microsoft-decision-1"]) == 0
    report = as_object(read_json(capsys.readouterr().out), "report")
    assert (
        as_object(report["model"], "model")["requested"]
        == "microsoft/microsoft-decision-1"
    )


def test_invalid_prepared_function_facts_fail_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "functions.json"
    source.write_text(
        '[{"path":"../outside.py","name":"restore","signature":"restore()","docstring":"Intent","body":"restore_worker()","line_start":1,"line_end":3}]'
    )
    assert main(["review", "--docstrings", str(source)]) == 2
    assert "relative" in capsys.readouterr().err
