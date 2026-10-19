"""Check declaration behavior at the repository boundary."""

from pathlib import Path

import pytest

from docreview.configuration import load_config
from docreview.frontmatter import REJECTED_KEYS, validate_document


def test_lifecycle_requires_status(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text('[review]\nauthority = "owned"\n')
    config = load_config(tmp_path)
    result = validate_document(
        config, "docs/design/proposal.md", "---\ntype: design\n---\n# Proposal\n"
    )
    assert [failure.rule for failure in result.failures] == [
        "frontmatter.status_missing"
    ]
    assert result.document_type == "design"


@pytest.mark.parametrize("key,reason", REJECTED_KEYS.items())
def test_rejected_keys_explain_the_rule(tmp_path: Path, key: str, reason: str) -> None:
    result = validate_document(
        load_config(tmp_path),
        "docs/usage/recover.md",
        f"---\ntype: runbook\n{key}: value\n---\n# Recovery\n",
    )
    finding = next(
        item for item in result.failures if item.rule == "frontmatter.unknown_key"
    )
    assert reason in finding.message
    assert finding.line_start == 3


@pytest.mark.parametrize(
    "content,rule",
    [
        ("type: runbook\nstatus: active", "frontmatter.status_invalid"),
        ("type: design\nstatus: installed", "frontmatter.status_invalid"),
        ("type: runbook\ntags: [one, two]", "frontmatter.scalar"),
        ("type: runbook\n  nested: value", "frontmatter.scalar"),
        ("type: runbook\ntype: runbook", "frontmatter.duplicate_key"),
        ("type: alien", "frontmatter.type_unknown"),
        ("type: runbook\nsuperseded_by: next", "frontmatter.unknown_key"),
    ],
)
def test_invalid_frontmatter_has_located_failures(
    tmp_path: Path, content: str, rule: str
) -> None:
    result = validate_document(
        load_config(tmp_path),
        "docs/usage/recover.md",
        f"---\n{content}\n---\n# Recovery\n",
    )
    assert rule in [item.rule for item in result.failures]
    assert all(item.line_start >= 1 for item in result.failures)


def test_fixed_name_precedes_frontmatter_and_path(tmp_path: Path) -> None:
    result = validate_document(
        load_config(tmp_path),
        "docs/usage/README.md",
        "---\ntype: runbook\n---\n# Recovery\n",
    )
    assert result.document_type == "readme"
    assert result.type_source == "fixed_name"
    assert result.failures[0].rule == "frontmatter.forbidden"


def test_missing_type_does_not_hide_path_resolution(tmp_path: Path) -> None:
    result = validate_document(
        load_config(tmp_path), "docs/usage/recover.md", "# Recovery\n"
    )
    assert result.document_type == "runbook"
    assert [item.rule for item in result.failures] == ["frontmatter.type_missing"]


def test_runtime_schema_is_not_document_frontmatter(tmp_path: Path) -> None:
    result = validate_document(
        load_config(tmp_path),
        ".agents/skills/run/SKILL.md",
        "---\nname: run\ndescription: Run safely\n---\n# Run\n",
    )
    assert result.type_source == "runtime_schema"
    assert not result.failures


def test_moved_type_path_conflict_requires_ownership_review(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    text = "---\ntype: design\nstatus: proposed\n---\n# Design\n"
    result = validate_document(config, "docs/usage/proposal.md", text, is_move=True)
    assert not result.failures
    assert result.review_items[0].rule == "placement.type_path"
    assert (
        validate_document(config, "docs/usage/proposal.md", text).failures[0].rule
        == "placement.type_path"
    )


@pytest.mark.parametrize(
    "label", ["**Status:** installed", "**Status**: installed", "Status: installed"]
)
def test_inline_status_has_one_authoritative_home(tmp_path: Path, label: str) -> None:
    text = f"---\ntype: design\nstatus: proposed\n---\n# Design\n\n{label}\n"
    result = validate_document(load_config(tmp_path), "docs/design/proposal.md", text)
    assert [(item.rule, item.line_start) for item in result.failures] == [
        ("frontmatter.inline_status", 7)
    ]


@pytest.mark.parametrize(
    "fragment",
    [
        "surprise = true",
        "[review]\nsurprise = true",
        "[types.runbook]\nsurprise = true",
        "[limits]\nsurprise = 1",
        '[review]\nengine = "docreview==99.0.0"',
        "[limits]\nrequest_bytes = 32001",
        '[review]\nauthority = "root"',
        "[thresholds]\naccept_above = nan",
        '[scope]\nmaintained = ["../private/*.md"]',
    ],
)
def test_bad_declaration_is_not_silently_accepted(
    tmp_path: Path, fragment: str
) -> None:
    (tmp_path / "docs-review.toml").write_text(fragment)
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_contributed_forces_advisory(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text(
        '[review]\nauthority = "contributed"\n[enforcement]\nplain_english = {level = "enforcing", issue = 123}\n'
    )
    config = load_config(tmp_path)
    assert config.enforcement["plain_english"].level == "advisory"
    assert config.enforcement["plain_english"].issue == 123


def test_custom_types_use_the_common_rubric(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text(
        '[types.narrative]\npaths = ["docs/history/**"]\nrubric = "common"\n'
    )
    config = load_config(tmp_path)
    result = validate_document(
        config, "docs/history/company.md", "---\ntype: narrative\n---\n# Company\n"
    )
    assert result.document_type == "narrative"
    assert not result.failures


def test_doc_named_agents_is_not_a_runtime_schema(tmp_path: Path) -> None:
    result = validate_document(
        load_config(tmp_path), "docs/agents/guide.md", "# Guide\n"
    )
    assert "frontmatter.type_missing" in [item.rule for item in result.failures]


def test_inline_status_example_in_code_is_not_a_second_status(tmp_path: Path) -> None:
    text = "---\ntype: design\nstatus: proposed\n---\n# Proposal\n\n```md\n**Status:** example\n```\n"
    result = validate_document(load_config(tmp_path), "docs/design/proposal.md", text)
    assert not result.failures


def test_loader_warns_when_a_declared_glob_has_no_files(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text(
        '[types.runbook]\npaths = ["docs/operations/**"]\n'
    )
    assert any(
        "docs/operations/**" in warning for warning in load_config(tmp_path).warnings
    )


def test_invalid_term_pattern_is_a_declaration_error(tmp_path: Path) -> None:
    (tmp_path / "docs-review.toml").write_text(
        '[terms]\nsources = [{file = "CONTEXT.md", pattern = "(", split = ", "}]\n'
    )
    with pytest.raises(ValueError, match="pattern"):
        load_config(tmp_path)


def test_optional_unresolved_type_remains_unknown_for_model_classification(
    tmp_path: Path,
) -> None:
    result = validate_document(
        load_config(tmp_path), "notes/idea.md", "# Idea\n\nAn undeclared note.\n"
    )
    assert result.document_type is None
    assert not result.failures
