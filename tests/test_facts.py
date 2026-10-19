"""Exercise facts and exit codes against a small repository, without model calls."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from docreview.cli import main
from docreview.configuration import load_config
from docreview.documentation_decisions import Json, as_object
from docreview.facts import inspect_repository
from docreview.markdown import sections


def test_orphan_and_banned_term_have_locations(tmp_path: Path) -> None:
    (tmp_path / "docs/usage").mkdir(parents=True)
    (tmp_path / "docs-review.toml").write_text(
        '[review]\nauthority = "owned"\n[terms]\nsources = [{file = "RETIRED.md", pattern = "^- (.*)$", split = ", ", scope = "global"}]\n'
    )
    (tmp_path / "RETIRED.md").write_text("# Retired words\n\n- backlog manager\n")
    (tmp_path / "docs/usage/recover.md").write_text(
        "---\ntype: runbook\n---\n# Recover\n\nUse the backlog manager.\n"
        + "Recovery steps. " * 100
    )
    report = inspect_repository(
        load_config(tmp_path),
        ["docs/usage/recover.md"],
        new_paths={"docs/usage/recover.md"},
    )
    documents = report["documents"]
    assert isinstance(documents, list)
    row = as_object(documents[0], "document")
    assert row["route"] == "revise"
    raw_failures = row["hard_failures"]
    assert isinstance(raw_failures, list)
    failures = [as_object(item, "failure") for item in raw_failures]
    assert {item["rule"] for item in failures} == {"navigation.orphan", "terms.banned"}
    assert (
        next(item for item in failures if item["rule"] == "terms.banned")["line_start"]
        == 6
    )


def write(root: Path, path: str, text: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)


def document(report: dict[str, Json], index: int = 0) -> dict[str, Json]:
    rows = report["documents"]
    assert isinstance(rows, list)
    return as_object(rows[index], "document")


def failures(row: dict[str, Json]) -> list[dict[str, Json]]:
    values = row["hard_failures"]
    assert isinstance(values, list)
    return [as_object(item, "failure") for item in values]


def test_curated_inbound_link_satisfies_new_runbook(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recovery\n" + "Recover the worker safely. " * 40,
    )
    write(tmp_path, "CONTEXT-MAP.md", "# Tasks\n\n[Recovery](docs/usage/recover.md)\n")
    row = document(
        inspect_repository(
            load_config(tmp_path),
            ["docs/usage/recover.md"],
            new_paths={"docs/usage/recover.md"},
        )
    )
    assert not failures(row)
    assert row["route"] == "review"
    assert "facts-only" in str(row["not_assessed"])


def test_fragment_and_reference_links_resolve_but_fences_are_ignored(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "README.md",
        "# Start\n\n[Recover][steps]\n[steps]: docs/usage/recover.md#recover-again\n\n```md\n[Missing](missing.md)\n```\n",
    )
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recovery\n\n## Recover again\n\nRecover.\n",
    )
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert not failures(row)
    facts = as_object(row["facts"], "facts")
    assert facts["links_total"] == 1


def test_broken_anchor_and_link_are_located(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        "# Start\n\n[Missing](nowhere.md)\n[Bad anchor](#no-such-heading)\n",
    )
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert [(f["rule"], f["line_start"]) for f in failures(row)] == [
        ("links.unresolved", 3),
        ("links.unresolved", 4),
    ]


def test_redirect_chain_and_cycle_are_checked(tmp_path: Path) -> None:
    for path, target in [
        ("README.md", "docs/README.md"),
        ("docs/README.md", "usage/README.md"),
        ("docs/usage/README.md", "../../README.md"),
    ]:
        write(tmp_path, path, f"# Moved\n\nMoved: [current]({target})\n")
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert failures(row)[0]["rule"] == "navigation.redirect_chain"
    write(tmp_path, "docs-review.toml", "[limits]\nredirect_chain = 10\n")
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert failures(row)[0]["rule"] == "navigation.redirect_cycle"


def test_misplaced_evidence_is_not_accepted_as_a_report(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/development/receipt.md",
        "---\ntype: report\n---\n# Deployment receipt\n\n2026-10-17T10:30:00 exit_code=0\n",
    )
    row = document(
        inspect_repository(load_config(tmp_path), ["docs/development/receipt.md"])
    )
    assert failures(row)[0]["rule"] == "placement.type_path"
    facts = as_object(row["facts"], "facts")
    assert facts["execution_log_signals"]


def test_quoted_terms_and_definitions_are_not_banned_occurrences(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "CONTEXT.md",
        '# Language\n\n_Avoid_: backlog manager\n\n> backlog manager\n\nUse "backlog manager" as the quoted example.\n\n`backlog manager`\n',
    )
    write(
        tmp_path,
        "docs-review.toml",
        '[terms]\nsources = [{file = "CONTEXT.md", pattern = "^_Avoid_: (.*)$", split = ", "}]\n',
    )
    row = document(inspect_repository(load_config(tmp_path), ["CONTEXT.md"]))
    assert not failures(row)


def test_section_ranges_do_not_include_fenced_headings() -> None:
    text = "# One\n\nFirst.\n```md\n## Example\n```\n## Two\n\nSecond.\n"
    outline = sections(text)
    assert [(s.heading, s.line_start, s.line_end) for s in outline] == [
        ("One", 1, 6),
        ("Two", 7, 9),
    ]


@pytest.mark.parametrize("authority,expected", [("owned", 1), ("contributed", 0)])
def test_cli_exit_codes_preserve_authority(
    tmp_path: Path, authority: str, expected: int
) -> None:
    write(tmp_path, "docs-review.toml", f'[review]\nauthority = "{authority}"\n')
    write(tmp_path, "README.md", "# Start\n[Missing](missing.md)\n")
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["facts", "--root", str(tmp_path), "README.md"])
    assert code == expected
    assert json.loads(out.getvalue())["summary"]["hard_failures"] == 1


def test_contributed_mode_refuses_output_writes(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# Start\n")
    with redirect_stderr(io.StringIO()):
        code = main(["facts", "--root", str(tmp_path), "--out", "report.json"])
    assert code == 2
    assert not (tmp_path / "report.json").exists()


def test_failed_input_is_not_reported_as_completed(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nengine = "docreview==99"\n')
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as out:
        assert main(["facts", "--root", str(tmp_path)]) == 2
    assert not out.getvalue()


def test_outside_symlinks_are_not_read(tmp_path: Path) -> None:
    with pytest.MonkeyPatch.context():
        (tmp_path / "README.md").symlink_to("/etc/hostname")
        with pytest.raises(ValueError, match="leaves repository"):
            inspect_repository(load_config(tmp_path))


def test_broken_moved_stub_is_a_finding_not_an_aborted_run(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# Moved\n\nMoved: [new](../../outside.md)\n")
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert failures(row)[0]["rule"] == "links.unresolved"


def test_excluded_documents_do_not_supply_an_inbound_route(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recover\n" + "Recover safely. " * 60,
    )
    write(tmp_path, "docs/fixtures/CONTEXT-MAP.md", "[Recover](../usage/recover.md)\n")
    row = document(
        inspect_repository(
            load_config(tmp_path),
            ["docs/usage/recover.md"],
            new_paths={"docs/usage/recover.md"},
        )
    )
    assert failures(row)[0]["rule"] == "navigation.orphan"


def test_headings_retain_inline_code_in_their_title_and_anchor(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# `docreview` CLI\n\n[Here](#docreview-cli)\n")
    row = document(inspect_repository(load_config(tmp_path), ["README.md"]))
    assert not failures(row)
    outline = sections((tmp_path / "README.md").read_text())
    assert outline[0].heading == "`docreview` CLI"


def test_bad_declaration_message_names_the_rejected_key(tmp_path: Path) -> None:
    write(tmp_path, "docs-review.toml", '[review]\nathority = "owned"\n')
    with redirect_stderr(io.StringIO()) as error:
        assert main(["facts", "--root", str(tmp_path)]) == 2
    assert "review: unknown keys: athority" in error.getvalue()


GLOSSARY = (
    "# Language\n\n"
    "**Narrative Person**:\nA person in the story.\n"
    "_Avoid_: Range user, operator,\nservice principal\n\n"
    "**Operator**:\nA person who performs range work.\n"
)
CONCEPT_SOURCE = (
    '[review]\nauthority = "owned"\n[terms]\nsources = [{file = "CONTEXT.md", '
    'pattern = "^_Avoid_: (.*)$", split = ", ", concept_pattern = "^\\\\*\\\\*([^*]+)\\\\*\\\\*:"}]\n'
)


def test_concept_scoped_terms_are_advisory_usage_facts(tmp_path: Path) -> None:
    write(tmp_path, "CONTEXT.md", GLOSSARY)
    write(tmp_path, "docs-review.toml", CONCEPT_SOURCE)
    write(
        tmp_path,
        "docs/usage/run.md",
        "---\ntype: runbook\n---\n# Run\n\nThe operator starts the range.\n",
    )
    row = document(inspect_repository(load_config(tmp_path), ["docs/usage/run.md"]))
    assert not [item for item in failures(row) if item["rule"] == "terms.banned"]
    facts = as_object(row["facts"], "facts")
    assert facts["banned_terms"] == []
    assert facts["term_usage"] == [
        {
            "term": "operator",
            "concept": "Narrative Person",
            "line_start": 6,
            "line_end": 6,
        }
    ]


def test_wrapped_avoid_entries_are_joined_and_not_counted_in_the_glossary(
    tmp_path: Path,
) -> None:
    write(tmp_path, "CONTEXT.md", GLOSSARY)
    write(tmp_path, "docs-review.toml", CONCEPT_SOURCE)
    write(
        tmp_path,
        "docs/usage/run.md",
        "---\ntype: runbook\n---\n# Run\n\nAsk the service principal.\n",
    )
    config = load_config(tmp_path)
    run = document(inspect_repository(config, ["docs/usage/run.md"]))
    usage = as_object(run["facts"], "facts")["term_usage"]
    assert isinstance(usage, list)
    assert [as_object(item, "usage")["term"] for item in usage] == ["service principal"]
    glossary = document(inspect_repository(config, ["CONTEXT.md"]))
    assert as_object(glossary["facts"], "facts")["term_usage"] == []


def test_global_scope_rejects_concept_pattern(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs-review.toml",
        '[terms]\nsources = [{file = "CONTEXT.md", pattern = "^- (.*)$", split = ", ", '
        'scope = "global", concept_pattern = "^# (.*)$"}]\n',
    )
    write(tmp_path, "CONTEXT.md", "# Words\n")
    with pytest.raises(ValueError, match="concept-scoped"):
        load_config(tmp_path)
