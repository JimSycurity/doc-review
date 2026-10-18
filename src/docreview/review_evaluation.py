"""Compare local labels and prior results without sending either to the model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docreview.documentation_decisions import Json, as_object

if TYPE_CHECKING:
    from docreview.documentation_inputs import Case


def _increment(matrix: dict[str, Json], expected: str, actual: str) -> None:
    row = as_object(matrix.setdefault(expected, {}), "confusion row")
    count = row.get(actual, 0)
    if not isinstance(count, int):
        raise TypeError("confusion count must be an integer")
    row[actual] = count + 1


def _agreement(
    rows: list[dict[str, Json]], previous: dict[str, Json] | None
) -> dict[str, Json]:
    old: Json = previous.get("documents", []) if previous else []
    if not isinstance(old, list):
        raise TypeError("prior documents must be a list")
    indexed = {
        (row.get("case_id"), row.get("path")): row
        for value in old
        if isinstance((row := value), dict)
    }
    compared = agreed = criteria_compared = criteria_agreed = 0
    for row in rows:
        prior = indexed.get((row.get("case_id"), row.get("path")))
        if prior is None:
            continue
        compared += 1
        agreed += row["route"] == prior.get("route")
        prior_criteria = as_object(prior.get("criteria", {}), "prior criteria")
        for key, value in as_object(row["criteria"], "criteria").items():
            before = prior_criteria.get(key)
            if isinstance(before, dict):
                criteria_compared += 1
                criteria_agreed += as_object(value, "criterion")[
                    "status"
                ] == before.get("status")
    return {
        "compared_documents": compared,
        "agreed_documents": agreed,
        "compared_criteria": criteria_compared,
        "agreed_criteria": criteria_agreed,
    }


def evaluate(
    cases: list[Case],
    rows: list[dict[str, Json]],
    *,
    live: bool,
    previous: dict[str, Json] | None,
) -> dict[str, Json]:
    """Expected routes and criteria are evaluation data, never review instructions."""
    routes: dict[str, Json] = {}
    by_type: dict[str, Json] = {}
    by_criterion: dict[str, Json] = {}
    labeled = {
        case.name: case
        for case in cases
        if case.expected_route or case.expected_criteria
    }
    if live:
        for row in rows:
            case = labeled.get(str(row["case_id"]))
            if case is not None:
                _case_scores(case, row, routes, by_type, by_criterion)
    return {
        "assessed": live,
        "labeled_documents": len(labeled),
        "routes": routes,
        "by_type": by_type,
        "by_criterion": by_criterion,
        "run_to_run_agreement": _agreement(rows, previous) if live else None,
    }


def _case_scores(
    case: Case,
    row: dict[str, Json],
    routes: dict[str, Json],
    by_type: dict[str, Json],
    by_criterion: dict[str, Json],
) -> None:
    if case.expected_route:
        _increment(routes, case.expected_route, str(row["route"]))
        type_matrix = as_object(by_type.setdefault(str(row["type"]), {}), "type matrix")
        _increment(type_matrix, case.expected_route, str(row["route"]))
    criteria = as_object(row["criteria"], "criteria")
    for key, expected in case.expected_criteria.items():
        matrix = as_object(by_criterion.setdefault(key, {}), "criterion matrix")
        matches = [
            as_object(value, "criterion")
            for name, value in criteria.items()
            if name == key or as_object(value, "criterion").get("family") == key
        ]
        if not matches:
            _increment(matrix, expected, "not_assessed")
        for criterion in matches:
            _increment(matrix, expected, str(criterion["status"]))
