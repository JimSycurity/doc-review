"""Exercise bounded, self-contained review inputs without a provider call."""

from pathlib import Path

from docreview.configuration import load_config
from docreview.documentation_decisions import Json, encoded_request
from docreview.documentation_inputs import Document
from docreview.review_requests import plan_document


def test_section_questions_name_their_own_input_and_purpose(tmp_path: Path) -> None:
    document = Document(
        "docs/usage/recover.md",
        "---\ntype: runbook\n---\n# Recovery\n\nRecover a worker.\n\n## Check\n\nCheck the saved configuration.\n",
        purpose="Recover a worker safely",
        audience="range operators",
        document_type="human_instructions",
        context_complete=True,
    )
    plan = plan_document(load_config(tmp_path), document, "runbook", {})
    assert plan.requests
    for request in plan.requests:
        assert len(encoded_request(request.payload)) <= 32000
        for question in request.questions:
            if question.family == "section_purpose_fit":
                assert "Recover a worker safely" in question.instructions
                assert "runbook" in question.instructions
                assert "state.sections." in question.instructions
                assert "that purpose" not in question.instructions


def test_large_document_bundles_keep_every_section_whole(tmp_path: Path) -> None:
    from docreview.documentation_decisions import as_object
    from docreview.markdown import sections

    text = "# Inventory\n\nUnderstand inventory.\n\n" + "".join(
        f"## Task {number}\n\n"
        + ("Check the saved inventory before replacing the worker. " * 25)
        + "\n\n"
        for number in range(25)
    )
    document = Document(
        "README.md", text, purpose="Understand inventory", document_type="readme"
    )
    plan = plan_document(load_config(tmp_path), document, "readme", {})
    assert not plan.gaps
    assert len(plan.requests) > 1
    observed: dict[str, str] = {}
    for request in plan.requests:
        assert len(encoded_request(request.payload)) <= 32000
        state = as_object(request.payload["state"], "state")
        outline = state["outline"]
        assert isinstance(outline, list)
        assert len(outline) == 26
        for value in as_object(state["sections"], "sections").values():
            section = as_object(value, "section")
            assert isinstance(section["text"], str)
            observed[str(section["heading"])] = section["text"]
    lines = text.splitlines(keepends=True)
    assert observed == {
        section.heading: "".join(lines[section.line_start - 1 : section.line_end])
        for section in sections(text)
    }


def test_oversized_individual_section_is_a_located_gap(tmp_path: Path) -> None:
    document = Document(
        "README.md",
        "# Tool\n\nIntro.\n\n## Unfit\n\n" + "é" * 17000,
        document_type="readme",
    )
    plan = plan_document(load_config(tmp_path), document, "readme", {})
    assert plan.gaps
    assert all(gap.location.heading == "Unfit" for gap in plan.gaps)
    assert all(
        len(encoded_request(request.payload)) <= 32000 for request in plan.requests
    )
    assert all("é" * 17000 not in str(request.payload) for request in plan.requests)


def test_duplicate_request_uses_only_candidate_sections(tmp_path: Path) -> None:
    from docreview.documentation_decisions import as_object

    source = "# Tool\n\nUse the saved inventory to restore the worker.\n"
    reference = "# Other\n\nDo not send unrelated private context.\n\n## Restore\n\nUse the saved inventory to restore the worker.\n"
    facts: dict[str, Json] = {
        "duplicate_candidates": [
            {
                "left": {
                    "path": "README.md",
                    "heading": "Tool",
                    "line_start": 1,
                    "line_end": 3,
                },
                "right": {
                    "path": "other.md",
                    "heading": "Restore",
                    "line_start": 5,
                    "line_end": 7,
                },
            }
        ]
    }
    plan = plan_document(
        load_config(tmp_path),
        Document("README.md", source),
        "readme",
        facts,
        corpus={"README.md": source, "other.md": reference},
    )
    pair = next(
        request for request in plan.requests if request.strategy == "reference_pair"
    )
    state = as_object(pair.payload["state"], "state")
    assert "Do not send unrelated private context" not in str(state)
    assert "Use the saved inventory" in str(state["pairs"])
