"""Located criterion aggregation, routing and caller-owned author repair history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docreview.configuration import Enforcement
from docreview.documentation_decisions import Json, as_object
from docreview.review_routing import Bands, assess_duplicate, assess_question

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig
    from docreview.documentation_inputs import Case
    from docreview.review_execution import Executor
    from docreview.review_types import Gap


def assess_criteria(
    config: ProjectConfig, executor: Executor
) -> dict[str, dict[str, Json]]:
    bands = Bands.from_config(config)
    result = {}
    for key, question in executor.questions.items():
        answers = tuple(executor.answers.get(key, ()))
        if question.family == "duplicate_ownership":
            continue
        if not answers:
            row: dict[str, Json] = {
                "question": question.instructions,
                "family": question.family,
                "location": question.location.as_json(),
                "status": "uncertain",
                "probability_met": None,
                "answer": None,
            }
        elif question.family == "duplicate_similarity":
            owner_key = f"duplicate_ownership:{question.group}"
            owners = tuple(executor.answers.get(owner_key, ()))
            row = (
                assess_duplicate(
                    question, answers, executor.questions[owner_key], owners, bands
                )
                if owners
                else {
                    "family": "duplication",
                    "question": question.instructions,
                    "location": question.location.as_json(),
                    "status": "uncertain",
                    "probability_met": None,
                    "answer": answers[0].as_json(),
                }
            )
            key = f"duplication:{question.group}"
        else:
            row = assess_question(question, answers, bands)
        family = str(row["family"])
        row["enforcement"] = config.enforcement.get(family, Enforcement()).level
        if question.family == "placement_resolution" and row["status"] == "pass":
            selected = str(answers[0].value)
            actual = question.location.path.rsplit("/", 1)[0]
            if actual != selected and not actual.startswith(selected + "/"):
                row["status"] = "fail"
                row["proposed_directory"] = selected
        result[key] = row
    return result


def route_document(
    row: dict[str, Json],
    case: Case,
    criteria: dict[str, dict[str, Json]],
    gaps: tuple[Gap, ...],
    previous: dict[str, Json] | None,
) -> dict[str, Json]:
    failed = [
        key for key, criterion in criteria.items() if criterion["status"] == "fail"
    ]
    uncertain = [
        key for key, criterion in criteria.items() if criterion["status"] == "uncertain"
    ]
    hard = row["hard_failures"]
    if hard:
        return row
    if not case.document.complete or not case.document.context_complete:
        route, reasons = "review", ["incomplete_document_or_context"]
    elif failed:
        route, reasons = _repair_route(case, failed, criteria, previous)
    elif gaps or uncertain or not criteria:
        route, reasons = "review", ["unassessed_or_uncertain_requirements"]
    else:
        route, reasons = "pass", ["requirements_met"]
    prior = _prior_findings(case, failed, criteria, previous)
    row.update(
        route=route,
        reasons=list[Json](reasons),
        handoff={
            "target": None
            if route == "pass"
            else "authoring_agent"
            if route == "revise"
            else "documentation_review_agent",
            "failed": list(failed),
            "uncertain": list(uncertain),
            "prior_findings": prior,
            "located_items": [
                {**criteria[key], "criterion": key} for key in (*failed, *uncertain)
            ]
            + [gap.as_json() for gap in gaps],
            "instructions": "Resolve located findings within project authority. This report does not approve, edit or dispatch work.",
        },
    )
    return row


def _repair_route(
    case: Case,
    failed: list[str],
    criteria: dict[str, dict[str, Json]],
    previous: dict[str, Json] | None,
) -> tuple[str, list[Json]]:
    if case.repair_round == 0:
        return "revise", ["confident_requirement_failure"]
    if previous is None:
        return "review", ["repair_history_missing"]
    matches = [
        row
        for row in report_documents(previous)
        if (row.get("case_id"), row.get("path")) == (case.name, case.document.path)
    ]
    if not matches:
        return "review", ["repair_history_missing"]
    for row in matches:
        older = as_object(row.get("criteria"), "prior criteria")
        for key in failed:
            old = older.get(key)
            if (
                isinstance(old, dict)
                and old.get("status") == "fail"
                and _same_location(old.get("location"), criteria[key]["location"])
            ):
                return "review", ["author_repair_budget_exhausted"]
    return "revise", ["new_located_requirement_failure"]


def _prior_findings(
    case: Case,
    failed: list[str],
    criteria: dict[str, dict[str, Json]],
    previous: dict[str, Json] | None,
) -> list[Json]:
    if not case.repair_round or previous is None:
        return []
    matches: list[Json] = []
    for row in report_documents(previous):
        if row.get("case_id") == case.name:
            for key, value in as_object(row.get("criteria"), "prior criteria").items():
                if (
                    key in failed
                    and isinstance(value, dict)
                    and _same_location(value.get("location"), criteria[key]["location"])
                ):
                    matches.append({"criterion": key, **value})
    return matches


def _same_location(left: Json, right: Json) -> bool:
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and (left.get("path"), left.get("heading"))
        == (right.get("path"), right.get("heading"))
    )


def report_documents(report: dict[str, Json]) -> list[dict[str, Json]]:
    rows = report.get("documents", [])
    if not isinstance(rows, list):
        raise TypeError("report documents must be a list")
    return [as_object(row, "document") for row in rows]


def json_array(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise TypeError("report field must be a list")
    return value
