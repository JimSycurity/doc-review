"""Check the complete review boundary with controlled typed provider responses."""

from pathlib import Path

from docreview.configuration import default_config
from docreview.documentation_decisions import Json, as_object
from docreview.documentation_inputs import Case, Document
from docreview.review_engine import review_cases


def good_response(payload: dict[str, Json], _key: str) -> dict[str, Json]:
    answers: dict[str, Json] = {}
    for key, raw in as_object(payload["questions"], "questions").items():
        question = as_object(raw, "question")
        if question["type"] == "score":
            answers[key] = {
                "type": "score",
                "score": 2,
                "confidence": 1,
                "probabilities": {"0": 0, "1": 0, "2": 1},
            }
        else:
            answers[key] = {"type": "noul", "noul": 0 if key == "split_needed" else 1}
    return {
        "model": payload["model"],
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 0},
    }


def readme() -> Case:
    return Case(
        "readme",
        "development",
        Document(
            "README.md",
            "# Widget\n\nWidget compares inventory.\n\n## Start\n\nRun widget --help.\n",
            purpose="Introduce Widget",
            audience="new users",
            document_type="readme",
            context_complete=True,
        ),
    )


def test_all_confident_requirements_can_pass_without_live_calls(tmp_path: Path) -> None:
    report = review_cases(
        default_config(tmp_path),
        [readme()],
        api_key="synthetic-key",
        transport=good_response,
    )
    documents = report["documents"]
    assert isinstance(documents, list)
    row = as_object(documents[0], "document")
    assert row["route"] == "pass"
    assert row["criteria"]
    assert row["requests"]


def test_prepared_function_docstrings_use_the_same_boundary(tmp_path: Path) -> None:
    from docreview.docstrings import FunctionDoc

    function = FunctionDoc(
        "src/worker.py",
        "restore",
        "def restore(snapshot: Path) -> None",
        "Restore the worker only after validating the snapshot signature.",
        "validate(snapshot)\nreplace_worker(snapshot)",
        10,
        17,
        required=True,
    )
    report = review_cases(
        default_config(tmp_path),
        [],
        functions=(function,),
        api_key="synthetic-key",
        transport=good_response,
    )
    values = report["documents"]
    assert isinstance(values, list)
    row = as_object(values[0], "document")
    assert row["route"] == "pass"
    assert row["type_source"] == "python-docstring"
    assert "docstring_informativeness:restore" in as_object(row["criteria"], "criteria")


def test_labeled_evaluation_records_confusion_and_repeat_agreement(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    case = replace(
        readme(), expected_route="pass", expected_criteria={"type_fit": "pass"}
    )
    config = default_config(tmp_path)
    first = review_cases(
        config, [case], api_key="synthetic-key", transport=good_response
    )
    second = review_cases(
        config, [case], api_key="synthetic-key", transport=good_response, previous=first
    )
    evaluation = as_object(second["evaluation"], "evaluation")
    assert (
        as_object(as_object(evaluation["routes"], "routes")["pass"], "pass")["pass"]
        == 1
    )
    agreement = as_object(evaluation["run_to_run_agreement"], "agreement")
    assert agreement["agreed_documents"] == 1
    assert as_object(evaluation["by_type"], "types")["readme"]
    assert as_object(evaluation["by_criterion"], "criteria")["type_fit"]


def _row(report: dict[str, Json]) -> dict[str, Json]:
    documents = report["documents"]
    assert isinstance(documents, list)
    return as_object(documents[0], "document")


def test_dry_run_cannot_pass_or_invoke_transport(tmp_path: Path) -> None:
    def forbidden(_payload: dict[str, Json], _key: str) -> dict[str, Json]:
        raise AssertionError("dry-run called transport")

    report = review_cases(default_config(tmp_path), [readme()], transport=forbidden)
    assert _row(report)["route"] == "review"
    assert report["evaluation"] is None


def test_missing_snapshot_link_target_is_unknown_not_pass(tmp_path: Path) -> None:
    from dataclasses import replace

    case = readme()
    case = replace(
        case,
        document=replace(
            case.document, text=case.document.text + "\n[Guide](missing.md)\n"
        ),
    )
    row = _row(
        review_cases(
            default_config(tmp_path),
            [case],
            api_key="synthetic-key",
            transport=good_response,
        )
    )
    assert row["route"] == "review"
    assert not row["hard_failures"]
    assert row["coverage_gaps"]


def test_hard_fact_failure_skips_model(tmp_path: Path) -> None:
    case = Case(
        "bad",
        "real",
        Document("docs/usage/recover.md", "# Recover\n", context_complete=True),
    )

    def forbidden(_payload: dict[str, Json], _key: str) -> dict[str, Json]:
        raise AssertionError("hard failure called provider")

    row = _row(
        review_cases(
            default_config(tmp_path),
            [case],
            api_key="synthetic-key",
            transport=forbidden,
        )
    )
    assert row["route"] == "revise"
    assert not row["requests"]


def test_failed_boundary_repeat_cannot_pass_and_other_documents_continue(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    calls = 0

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise ValueError("controlled provider failure")
        result = good_response(payload, key)
        if calls == 1:
            as_object(result["answers"], "answers")["type_fit"] = {
                "type": "noul",
                "noul": 0.91,
            }
        return result

    report = review_cases(
        default_config(tmp_path),
        [readme(), replace(readme(), name="next")],
        api_key="synthetic-key",
        transport=response,
    )
    documents = report["documents"]
    assert isinstance(documents, list)
    assert as_object(documents[0], "first")["route"] == "review"
    assert as_object(documents[1], "second")["route"] == "pass"
    summary = as_object(report["summary"], "summary")
    assert summary["model_failures"] == 2
    assert summary["request_count"] == 4
    assert summary["request_strategies"] == {"whole": 4}


def test_boundary_sampling_uses_three_unique_nonces_and_retains_answers(
    tmp_path: Path,
) -> None:
    nonces: list[Json] = []

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        nonces.append(as_object(payload["state"], "state")["nonce"])
        result = good_response(payload, key)
        as_object(result["answers"], "answers")["type_fit"] = {
            "type": "noul",
            "noul": 0.91,
        }
        return result

    row = _row(
        review_cases(
            default_config(tmp_path),
            [readme()],
            api_key="synthetic-key",
            transport=response,
        )
    )
    assert len(nonces) == len(set(nonces)) == 3
    criterion = as_object(
        as_object(row["criteria"], "criteria")["type_fit"], "type fit"
    )
    samples = criterion["sample_answers"]
    assert isinstance(samples, list)
    assert len(samples) == 3
    assert row["route"] == "pass"


def test_same_located_failure_gets_one_author_round(tmp_path: Path) -> None:
    from dataclasses import replace

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        result = good_response(payload, key)
        as_object(result["answers"], "answers")["type_fit"] = {
            "type": "noul",
            "noul": 0,
        }
        return result

    config = default_config(tmp_path)
    first = review_cases(
        config, [readme()], api_key="synthetic-key", transport=response
    )
    assert _row(first)["route"] == "revise"
    second = review_cases(
        config,
        [replace(readme(), repair_round=1)],
        api_key="synthetic-key",
        transport=response,
        previous=first,
    )
    assert _row(second)["route"] == "review"
    assert _row(second)["reasons"] == ["author_repair_budget_exhausted"]


def test_type_disagreement_stops_rubric_questions(tmp_path: Path) -> None:
    observed: list[list[str]] = []

    def response(payload: dict[str, Json], _key: str) -> dict[str, Json]:
        questions = as_object(payload["questions"], "questions")
        assert list(questions) == ["document_type"]
        options = as_object(
            as_object(questions["document_type"], "type")["criteria"], "options"
        )
        observed.append(list(options))
        label = "readme" if len(observed) == 1 else "report"
        return {
            "model": payload["model"],
            "answers": {
                "document_type": {
                    "type": "choice",
                    "choice": label,
                    "confidence": 1,
                    "probabilities": {key: 1 if key == label else 0 for key in options},
                }
            },
        }

    case = Case(
        "unknown",
        "real",
        Document("notes/page.md", "# Page\n\nA task.\n", context_complete=True),
    )
    row = _row(
        review_cases(
            default_config(tmp_path),
            [case],
            api_key="synthetic-key",
            transport=response,
        )
    )
    assert row["route"] == "review"
    assert observed[0] == list(reversed(observed[1]))
    assert row["type"] is None


def test_required_docstring_failures_skip_model(tmp_path: Path) -> None:
    from docreview.docstrings import FunctionDoc

    functions = (
        FunctionDoc(
            "src/work.py",
            "restore",
            "def restore()",
            "",
            "restore_worker()",
            1,
            3,
            required=True,
        ),
        FunctionDoc(
            "src/work.py",
            "resetWorker",
            "def resetWorker()",
            "Reset worker.",
            "reset()",
            5,
            8,
        ),
    )
    report = review_cases(default_config(tmp_path), [], functions=functions)
    row = _row(report)
    assert row["route"] == "revise"
    failures = row["hard_failures"]
    assert isinstance(failures, list)
    assert {as_object(value, "failure")["rule"] for value in failures} == {
        "docstring.missing",
        "docstring.name_echo",
    }
    assert not row["requests"]


def test_moved_type_conflict_stays_with_ownership_review(tmp_path: Path) -> None:
    source = "---\ntype: report\n---\n# Result\n\nExplain the observed outcome.\n"
    case = Case(
        "moved",
        "real",
        Document("docs/design/result.md", source, context_complete=True),
    )
    row = _row(
        review_cases(
            default_config(tmp_path),
            [case],
            corpus={case.document.path: source},
            moved_paths={case.document.path},
            api_key="synthetic-key",
            transport=good_response,
        )
    )
    assert row["route"] == "review"
    assert not row["hard_failures"]
    assert row["coverage_gaps"]


def test_failure_cannot_be_canceled_by_successful_other_criteria(
    tmp_path: Path,
) -> None:
    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        result = good_response(payload, key)
        answers = as_object(result["answers"], "answers")
        answers["type_fit"] = {"type": "noul", "noul": 0}
        answers["audience_detail"] = {
            "type": "score",
            "score": 1,
            "confidence": 1,
            "probabilities": {"0": 0, "1": 1, "2": 0},
        }
        return result

    row = _row(
        review_cases(
            default_config(tmp_path),
            [readme()],
            api_key="synthetic-key",
            transport=response,
        )
    )
    assert row["route"] == "revise"
    handoff = as_object(row["handoff"], "handoff")
    failed = handoff["failed"]
    assert isinstance(failed, list)
    # A confident "partly fits" answer misses the top level, so it fails too.
    assert set(failed) == {"type_fit", "audience_detail"}
    assert handoff["uncertain"] == []


def test_evaluation_labels_and_retry_count_never_enter_model_state(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from docreview.documentation_decisions import encoded_request

    case = replace(
        readme(),
        name="PRIVATE_CASE_LABEL",
        split="holdout",
        repair_round=4,
        expected_route="revise",
        expected_criteria={"PRIVATE_CRITERION": "fail"},
    )

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        encoded = encoded_request(payload)
        assert b"PRIVATE" not in encoded
        assert b"expected_route" not in encoded
        assert b"repair_round" not in encoded
        return good_response(payload, key)

    assert (
        _row(
            review_cases(
                default_config(tmp_path),
                [case],
                api_key="synthetic-key",
                transport=response,
            )
        )["route"]
        == "pass"
    )


def test_missing_matching_repair_history_routes_to_review(tmp_path: Path) -> None:
    from dataclasses import replace

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        result = good_response(payload, key)
        as_object(result["answers"], "answers")["type_fit"] = {
            "type": "noul",
            "noul": 0,
        }
        return result

    row = _row(
        review_cases(
            default_config(tmp_path),
            [replace(readme(), repair_round=1)],
            previous={"documents": []},
            api_key="synthetic-key",
            transport=response,
        )
    )
    assert row["route"] == "review"
    assert row["reasons"] == ["repair_history_missing"]


def test_snapshot_can_check_external_target_only_with_explicit_root(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    (tmp_path / "guide.md").write_text("# Guide\n\nStable usage.\n")
    case = readme()
    case = replace(
        case,
        document=replace(
            case.document, text=case.document.text + "\n[Guide](guide.md)\n"
        ),
    )
    row = _row(
        review_cases(
            default_config(tmp_path),
            [case],
            snapshot_filesystem=True,
            api_key="synthetic-key",
            transport=good_response,
        )
    )
    assert row["route"] == "pass"


def test_changed_model_revision_cannot_pass(tmp_path: Path) -> None:
    calls = 0

    def response(payload: dict[str, Json], key: str) -> dict[str, Json]:
        nonlocal calls
        calls += 1
        result = good_response(payload, key)
        as_object(result["answers"], "answers")["type_fit"] = {
            "type": "noul",
            "noul": 0.91,
        }
        result["model"] = str(payload["model"]) + (
            "-edition1" if calls == 1 else "-edition2"
        )
        return result

    row = _row(
        review_cases(
            default_config(tmp_path),
            [readme()],
            api_key="synthetic-key",
            transport=response,
        )
    )
    assert row["route"] == "review"
    assert "revision changed" in str(row["not_assessed"])


def _snapshot(path: str, kind: str | None) -> Case:
    return Case(
        "snapshot",
        "development",
        Document(
            path,
            "# Recover\n\nRestore the worker.\n\n## Steps\n\n1. Stop it.\n2. Restore it.\n",
            context="Glossary.",
            document_type=kind,
            context_complete=True,
        ),
    )


def _rules(report: dict[str, Json]) -> list[object]:
    rows = report["documents"]
    assert isinstance(rows, list)
    failures = as_object(rows[0], "row")["hard_failures"]
    assert isinstance(failures, list)
    return [as_object(item, "failure")["rule"] for item in failures]


def test_snapshot_case_type_is_its_declaration(tmp_path: Path) -> None:
    report = review_cases(
        default_config(tmp_path), [_snapshot("docs/usage/recover.md", "runbook")]
    )
    rows = report["documents"]
    assert isinstance(rows, list)
    row = as_object(rows[0], "row")
    assert _rules(report) == []
    assert row["requests"], "a declared snapshot reaches the model stage"
    notes = row["not_assessed"]
    assert isinstance(notes, list)
    assert any("supplied by the case input" in str(note) for note in notes)


def test_snapshot_without_declared_type_keeps_the_declaration_rule(
    tmp_path: Path,
) -> None:
    report = review_cases(
        default_config(tmp_path), [_snapshot("docs/usage/recover.md", None)]
    )
    assert "frontmatter.type_missing" in _rules(report)


def test_snapshot_placement_failure_still_short_circuits(tmp_path: Path) -> None:
    report = review_cases(
        default_config(tmp_path), [_snapshot("docs/plans/recover.md", "runbook")]
    )
    assert _rules(report) == ["placement.type_path"]
