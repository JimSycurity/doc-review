"""Whole-document routing and API boundary tests; no live model-quality claims."""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from docreview import documentation_decisions as decisions
from docreview import documentation_trial as trial
from docreview.documentation_inputs import (
    Case,
    Document,
    Reference,
    from_snapshots,
    load_cases,
    parse_case,
)
from docreview.documentation_rubrics import TYPES, criteria_for


def example(kind: str | None = "readme") -> Case:
    """Provide a complete declared-role document for routing tests."""
    return Case(
        "readme",
        "development",
        Document(
            "README.md",
            "# Widget\n\nWidget compares inventory.\n\n## Start\nRun widget --help.\n",
            purpose="Introduce Widget and help a new user start.",
            audience="New users",
            context="README is the project entry point; setup.md owns detailed installation.",
            document_type=kind,
            context_complete=True,
        ),
    )


def response(
    document: Document, kind: str = "readme", overrides: dict[str, float] | None = None
) -> dict[str, decisions.Json]:
    """Mock known observations for policy tests; never present them as model evidence."""
    criteria = criteria_for(kind, bool(document.context), bool(document.references))
    overrides = overrides or {}
    answers: dict[str, decisions.Json] = {}
    for key, criterion in criteria.items():
        if criterion.levels:
            answers[key] = {
                "type": "score",
                "score": 2,
                "confidence": 1,
                "probabilities": {"0": 0, "1": 0, "2": 1},
            }
        else:
            answers[key] = {
                "type": "noul",
                "noul": overrides.get(key, 1.0 if criterion.positive else 0.0),
            }
    return {"model": "test-decision-model", "answers": answers}


def run_case(
    case: Case, overrides: dict[str, float] | None = None
) -> dict[str, decisions.Json]:
    """Run the real routing code against a controlled valid response."""
    with patch.object(
        trial,
        "decide",
        return_value=response(
            case.document, case.document.document_type or "readme", overrides
        ),
    ):
        return trial.review_case(
            case, trial.DEFAULT_MODEL, "test-token", trial.Thresholds()
        )


class TestRouting:
    def test_confident_compliance_passes(self) -> None:
        assert run_case(example())["route"] == "pass"

    def test_confident_failure_returns_to_author(self) -> None:
        result = run_case(example(), {"scope_fit": 0.01})
        assert result["route"] == "revise"
        handoff = decisions.as_object(result["handoff"], "handoff")
        assert handoff["target"] == "authoring_agent"
        failed = handoff["failed_criteria"]
        assert isinstance(failed, list)
        assert "scope_fit" in failed

    def test_uncertain_answer_goes_to_reviewer_not_author(self) -> None:
        assert run_case(example(), {"scope_fit": 0.5})["route"] == "review"

    def test_structure_failure_cannot_be_cancelled_by_high_composite(self) -> None:
        result = run_case(example(), {"split_needed": 0.99})
        assert result["composite_score"] == 1.0
        assert result["route"] == "revise"

    def test_jargon_failure_routes_to_author_even_with_other_checks_met(self) -> None:
        case = example("design")
        payload = response(case.document, "design")
        answers = decisions.as_object(payload["answers"], "answers")
        answers["plain_english"] = {
            "type": "score",
            "score": 0,
            "confidence": 1,
            "probabilities": {"0": 1, "1": 0, "2": 0},
        }
        with patch.object(trial, "decide", return_value=payload):
            result = trial.review_case(
                case, trial.DEFAULT_MODEL, "test-token", trial.Thresholds()
            )
        assert result["route"] == "revise"

    def test_low_score_with_low_certainty_goes_to_review(self) -> None:
        bands = trial.Thresholds()
        assert bands.status(0.01, 0.99, 0.2) == "uncertain"

    def test_middle_score_is_not_an_automatic_pass(self) -> None:
        assert trial.Thresholds().status(0, 0, 1) == "uncertain"

    def test_incomplete_context_or_document_never_passes(self) -> None:
        for document in (
            replace(example().document, complete=False),
            replace(example().document, context_complete=False),
        ):
            case = replace(example(), document=document)
            assert run_case(case)["route"] == "review"

    def test_missing_model_established_context_is_not_author_failure(self) -> None:
        assert run_case(example(), {"context_sufficient": 0.01})["route"] == "review"

    def test_uncertainty_does_not_hide_a_confident_specific_failure(self) -> None:
        result = run_case(example(), {"context_sufficient": 0.5, "scope_fit": 0.01})
        assert result["route"] == "revise"

    def test_second_failed_author_round_goes_to_reviewer(self) -> None:
        result = run_case(replace(example(), repair_round=1), {"scope_fit": 0.01})
        assert result["route"] == "review"
        reasons = result["reasons"]
        assert isinstance(reasons, list)
        assert "author_repair_budget_exhausted" in reasons

    def test_unknown_type_confidence_routes_to_review_without_scoring(self) -> None:
        options: dict[str, decisions.Json] = {**dict.fromkeys(TYPES, 0), "unknown": 1}
        payload: dict[str, decisions.Json] = {
            "model": "test",
            "answers": {
                "document_type": {
                    "type": "choice",
                    "choice": "unknown",
                    "confidence": 1,
                    "probabilities": options,
                }
            },
        }
        with patch.object(trial, "decide", return_value=payload) as api:
            result = trial.review_case(
                example(None), trial.DEFAULT_MODEL, "test-token", trial.Thresholds()
            )
        assert result["route"] == "review"
        assert api.call_count == 1
        assert "criteria" not in result

    def test_confident_type_classification_selects_its_rubric(self) -> None:
        options: dict[str, decisions.Json] = dict.fromkeys([*TYPES, "unknown"], 0.0)
        options["readme"] = 1
        classification: dict[str, decisions.Json] = {
            "model": "test",
            "answers": {
                "document_type": {
                    "type": "choice",
                    "choice": "readme",
                    "confidence": 1,
                    "probabilities": options,
                }
            },
        }
        with patch.object(
            trial, "decide", side_effect=[classification, response(example().document)]
        ) as api:
            result = trial.review_case(
                example(None), trial.DEFAULT_MODEL, "test-token", trial.Thresholds()
            )
        assert result["route"] == "pass"
        assert api.call_count == 2
        assert "getting_started" in decisions.as_object(result["criteria"], "criteria")

    def test_api_error_is_a_review_handoff_and_does_not_abort_other_documents(
        self,
    ) -> None:
        with patch.object(
            trial,
            "decide",
            side_effect=[
                ValueError("Decision API returned HTTP 413; response body withheld"),
                response(example().document),
            ],
        ):
            report = trial.run_trial(
                [example(), replace(example(), name="next")],
                trial.DEFAULT_MODEL,
                "test-token",
                trial.Thresholds(),
            )
        assert report["processed_documents"] == 2
        assert report["model_failures"] == 1
        rows = report["results"]
        assert isinstance(rows, list)
        assert decisions.as_object(rows[0], "result")["route"] == "review"
        assert decisions.as_object(rows[1], "result")["route"] == "pass"

    def test_all_document_types_have_specific_checks(self) -> None:
        for kind in TYPES:
            criteria = criteria_for(kind, True, True)
            assert "split_needed" in criteria
            assert "canonical_ownership" in criteria
            assert len(criteria) > 12
        assert "plain_english" in criteria_for("design", False, False)
        assert "summary" in criteria_for("report", False, False)

    def test_invalid_thresholds_fail(self) -> None:
        for reject, accept, confidence in [
            (-0.1, 0.9, 0.8),
            (0.6, 0.9, 0.8),
            (0.1, 0.4, 0.8),
            (0.1, 0.9, float("nan")),
            (0.1, 0.9, 1.1),
        ]:
            with pytest.raises(ValueError):
                trial.Thresholds(reject, accept, confidence)


class TestInput:
    def test_full_document_larger_than_old_cutoff_is_preserved(self) -> None:
        text = "# Whole document\n\n" + "A complete explanation.\n" * 3000
        document = from_snapshots(
            "guide.md", "Old.\n", text, document_type="human_instructions"
        )
        request = trial.preview(
            replace(example(), document=document), trial.DEFAULT_MODEL, False
        )
        state = decisions.as_object(request["state"], "state")
        assert state["document"] == text
        assert len(json.dumps(request)) > 16000
        assert "Old." in str(state["diff"])

    def test_heading_only_changes_keep_the_whole_result(self) -> None:
        document = from_snapshots("guide.md", "# Old\nBody.\n", "# New\nBody.\n")
        assert document.text == "# New\nBody.\n"
        assert "-# Old" in document.diff

    def test_reference_content_is_preserved(self) -> None:
        document = replace(
            example().document, references=(Reference("owner.md", "Canonical rule.\n"),)
        )
        assert document.state()["references"] == [
            {"path": "owner.md", "text": "Canonical rule.\n"}
        ]

    def test_request_excludes_expected_route_and_case_identity(self) -> None:
        case = replace(example(), name="hidden-label", expected_route="revise")
        request = trial.preview(case, trial.DEFAULT_MODEL, False)
        assert "hidden-label" not in json.dumps(request)
        assert "expected_route" not in json.dumps(request)

    def test_reversal_preserves_ordered_score_levels(self) -> None:
        normal = decisions.as_object(
            trial.preview(example(), trial.DEFAULT_MODEL, False)["questions"],
            "questions",
        )
        reverse = decisions.as_object(
            trial.preview(example(), trial.DEFAULT_MODEL, True)["questions"],
            "questions",
        )
        assert list(reverse) == list(reversed(normal))
        assert normal["plain_english"] == reverse["plain_english"]

    def test_dry_run_does_not_read_key_file_or_call_api(self) -> None:
        with patch.object(trial, "decide") as api, redirect_stdout(io.StringIO()):
            result = trial.main(["--key-file", "/missing/token"])
        assert result == 0
        api.assert_not_called()

    def test_missing_key_file_path_is_not_logged(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            result = trial.main(["--live", "--key-file", "/missing/secret"])
        assert result == 2
        assert "/missing/secret" not in error.getvalue()

    def test_invalid_document_kind_or_boolean_fails(self) -> None:
        invalid: list[dict[str, decisions.Json]] = [
            {"document_type": []},
            {"complete": "yes"},
            {"context_complete": 1},
        ]
        for extra in invalid:
            row = {
                "id": "one",
                "split": "development",
                "document": {"path": "doc.md", "text": "Whole text", **extra},
            }
            with pytest.raises(ValueError):
                parse_case(decisions.read_json(json.dumps(row)))

    def test_no_labels_means_no_accuracy_field(self) -> None:
        result = run_case(example())
        assert "matches_expected" not in result

    def test_duplicate_cases_fail(self) -> None:
        rows = json.loads(trial.CASE_FILE.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps([rows[0], rows[0]]))
            with pytest.raises(ValueError, match="unique"):
                load_cases(path, "all")


class TestDecisionBoundary:
    def test_missing_answers_fail(self) -> None:
        with pytest.raises(ValueError):
            decisions.read_answers(
                {"model": "test", "answers": {}}, {"one": {"type": "noul"}}
            )

    def test_bad_noul_values_fail(self) -> None:
        for value in (True, None, "0.9", -0.1, 1.1, float("nan")):
            with pytest.raises(ValueError):
                decisions.read_answer({"type": "noul", "noul": value}, {"type": "noul"})

    def test_confident_yes_and_confident_no_have_equal_certainty(self) -> None:
        yes = decisions.read_answer({"type": "noul", "noul": 0.99}, {"type": "noul"})
        no = decisions.read_answer({"type": "noul", "noul": 0.01}, {"type": "noul"})
        assert round(yes.confidence - no.confidence, 7) == 0
        assert yes.value != no.value

    def test_bad_score_distribution_or_legend_fails(self) -> None:
        question: dict[str, decisions.Json] = {
            "type": "score",
            "criteria": ["Poor", "Mixed", "Good"],
        }
        invalid: list[dict[str, decisions.Json]] = [
            {"score": 0},
            {"legend": {"0": "Wrong", "1": "Wrong", "2": "Wrong"}},
            {"probabilities": {"0": 1, "1": 1, "2": 1}},
            {"confidence": True},
        ]
        for extras in invalid:
            raw: dict[str, decisions.Json] = {
                "type": "score",
                "score": 2,
                "confidence": 1,
                "probabilities": {"0": 0, "1": 0, "2": 1},
                **extras,
            }
            with pytest.raises(ValueError):
                decisions.read_answer(raw, question)

    def test_choice_must_match_highest_probability(self) -> None:
        with pytest.raises(ValueError):
            decisions.read_answer(
                {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 1,
                    "probabilities": {"a": 0, "b": 1},
                },
                {"type": "choice", "criteria": {"a": "A", "b": "B"}},
            )

    def test_wrong_primitive_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            decisions.read_answer({"type": "score", "score": 1}, {"type": "noul"})

    def test_http_errors_do_not_log_provider_body(self) -> None:
        for status in (401, 302):
            with patch.object(decisions.http.client, "HTTPSConnection") as connection:
                reply = connection.return_value.getresponse.return_value
                reply.status = status
                reply.read.return_value = b"test-token"
                with pytest.raises(ValueError) as error:
                    decisions.decide({}, "test-token")
                reply.read.assert_not_called()
                assert "test-token" not in str(error.value)
                connection.return_value.close.assert_called_once()

    def test_bad_key_does_not_reach_network(self) -> None:
        with patch.object(decisions.http.client, "HTTPSConnection") as connection:
            with pytest.raises(ValueError):
                decisions.decide({}, "test-token\nmalformed")
            connection.assert_not_called()

    def test_authentication_key_is_never_sent_as_document_content(self) -> None:
        with patch.object(decisions.http.client, "HTTPSConnection") as connection:
            with pytest.raises(ValueError):
                decisions.decide({"state": "accidental test-token echo"}, "test-token")
            connection.assert_not_called()
