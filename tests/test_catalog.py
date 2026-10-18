"""Check generated navigation through the public package command."""

import io
from contextlib import redirect_stdout
from pathlib import Path

from docreview.cli import main


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_catalog_is_stable_and_uses_declarations_and_purpose(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/design/worker.md",
        "---\ntype: design\nstatus: proposed\n---\n# Worker recovery\n\nExplain how workers recover safely.\n\n## Details\nDo not copy this section into the index.\n",
    )
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
    catalog = tmp_path / "docs/design/README.md"
    before = catalog.read_bytes()
    assert "[Worker recovery](worker.md)" in before.decode()
    assert "design | proposed | Explain how workers recover safely." in before.decode()
    assert "Do not copy this section" not in before.decode()
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 0
    assert catalog.read_bytes() == before


def test_check_detects_stale_output_without_writing(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 1
    assert not (tmp_path / "docs/usage/README.md").exists()
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a changed worker.\n",
    )
    before = (tmp_path / "docs/usage/README.md").read_bytes()
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 1
    assert (tmp_path / "docs/usage/README.md").read_bytes() == before


def test_excluded_pages_are_omitted_and_curated_index_is_preserved(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "docs-review.toml",
        '[review]\nauthority = "owned"\n[types.narrative]\npaths = ["docs/history/**"]\nrubric = "common"\n',
    )
    write(
        tmp_path, "docs/history/README.md", "# KNG history\n\nHand-written routing.\n"
    )
    write(
        tmp_path,
        "docs/history/company.md",
        "---\ntype: narrative\n---\n# Company\n\nThe company narrative.\n",
    )
    write(tmp_path, "docs/fixtures/internal.md", "# Invalid fixture\n")
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 0
    assert (
        tmp_path / "docs/history/README.md"
    ).read_text() == "# KNG history\n\nHand-written routing.\n"
    parent = (tmp_path / "docs/README.md").read_text()
    assert "[history](history/README.md)" in parent
    assert "fixture" not in parent
    assert not (tmp_path / "docs/fixtures/README.md").exists()


def test_contributed_catalog_is_a_read_only_preview(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    with redirect_stdout(io.StringIO()) as output:
        assert main(["catalog", "--root", str(tmp_path)]) == 0
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 0
    assert "report-only" in output.getvalue()
    assert not (tmp_path / "docs/usage/README.md").exists()


def test_unmarked_readme_conflict_prevents_all_writes(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    write(tmp_path, "docs/usage/README.md", "# Human instructions\n\nKeep this.\n")
    with redirect_stdout(io.StringIO()) as output:
        assert main(["catalog", "--root", str(tmp_path)]) == 1
    assert "catalog.ownership" in output.getvalue()
    assert (
        tmp_path / "docs/usage/README.md"
    ).read_text() == "# Human instructions\n\nKeep this.\n"
    assert not (tmp_path / "docs/README.md").exists()


def test_missing_curated_index_does_not_generate_a_broken_directory_link(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "docs-review.toml",
        '[review]\nauthority = "owned"\n[types.narrative]\npaths = ["docs/history/**"]\nrubric = "common"\n',
    )
    write(
        tmp_path,
        "docs/history/company.md",
        "---\ntype: narrative\n---\n# Company\n\nThe company narrative.\n",
    )
    with redirect_stdout(io.StringIO()) as output:
        assert main(["catalog", "--root", str(tmp_path)]) == 1
    assert "catalog.curated_missing" in output.getvalue()
    assert not (tmp_path / "docs/README.md").exists()


def test_setext_title_uses_the_following_purpose_paragraph(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\nRecover\n=======\n\nRecover a worker safely.\n",
    )
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
    catalog = (tmp_path / "docs/usage/README.md").read_text()
    assert (
        "| [Recover](recover.md) | runbook | - | Recover a worker safely. |" in catalog
    )


def test_generation_report_distinguishes_written_output_from_staleness(
    tmp_path: Path,
) -> None:
    import json

    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    with redirect_stdout(io.StringIO()) as output:
        assert main(["catalog", "--root", str(tmp_path)]) == 0
    report = json.loads(output.getvalue())
    assert all(row["state"] == "current" for row in report["catalogs"])
    assert all(row["action"] == "written" for row in report["catalogs"])


def test_source_removal_makes_the_catalog_stale(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path)]) == 0
    (tmp_path / "docs/usage/recover.md").unlink()
    with redirect_stdout(io.StringIO()):
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 1
        assert main(["catalog", "--root", str(tmp_path)]) == 0
        assert main(["catalog", "--root", str(tmp_path), "--check"]) == 0
    assert "recover.md" not in (tmp_path / "docs/usage/README.md").read_text()


def test_catalog_rejects_an_edit_made_after_planning(tmp_path: Path) -> None:
    import pytest

    from docreview.catalog import apply_catalogs, plan_catalogs
    from docreview.configuration import load_config

    write(tmp_path, "docs-review.toml", '[review]\nauthority = "owned"\n')
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n\nRecover a worker.\n",
    )
    config = load_config(tmp_path)
    plan = plan_catalogs(config)
    write(tmp_path, "docs/usage/README.md", "# Human edit\n")
    with pytest.raises(ValueError, match="changed after inspection"):
        apply_catalogs(config, plan)
    assert (tmp_path / "docs/usage/README.md").read_text() == "# Human edit\n"
    assert not (tmp_path / "docs/README.md").exists()
