"""Join deterministic facts and located model evidence into one advisory report."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from docreview import __version__
from docreview.docstrings import plan_function
from docreview.documentation_decisions import Json, as_object, decide
from docreview.documentation_inputs import Case, Document
from docreview.facts import inspect_corpus
from docreview.review_evaluation import evaluate
from docreview.review_execution import Executor, Transport
from docreview.review_handoff import (
    assess_criteria,
    json_array,
    report_documents,
    route_document,
)
from docreview.review_questions import placement_question, role_name, type_question
from docreview.review_requests import plan_document, plan_resolution
from docreview.review_routing import Bands
from docreview.review_types import Gap, Location

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig
    from docreview.docstrings import FunctionDoc


def defined_terms(context: str) -> tuple[str, ...]:
    """Read glossary entries written as headings or as bold definition labels."""
    found: list[str] = []
    for line in context.splitlines():
        if line.startswith("## "):
            found.append(line.removeprefix("## ").strip())
        elif match := re.match(r"\*\*([^*]+)\*\*:", line):
            found.append(match[1].strip())
    return tuple(dict.fromkeys(term for term in found if term))


def review_cases(
    config: ProjectConfig,
    cases: list[Case],
    *,
    corpus: dict[str, str] | None = None,
    api_key: str | None = None,
    transport: Transport = decide,
    previous: dict[str, Json] | None = None,
    functions: tuple[FunctionDoc, ...] = (),
    new_paths: set[str] | None = None,
    moved_paths: set[str] | None = None,
    function_repair_round: int = 0,
    snapshot_filesystem: bool = False,
) -> dict[str, Json]:
    """Explicit credentials enable calls; otherwise every decision stays unassessed."""
    report: dict[str, Json] = {
        "policy_version": "docreview-1",
        "engine_version": __version__,
        "config_sha256": config.sha256,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "dry-run" if api_key is None else "live",
        "model": {"requested": config.model, "answered": None},
        "thresholds": Bands.from_config(config).as_json(),
        "warnings": list(config.warnings),
    }
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    selected = [case.document.path for case in cases]
    shared_rows: list[dict[str, Json]] = (
        report_documents(
            inspect_corpus(
                config, corpus, selected, new_paths=new_paths, moved_paths=moved_paths
            )
        )
        if corpus is not None and cases
        else []
    )
    indexed = {str(row["path"]): row for row in shared_rows}
    rows = [
        _review_case(
            config,
            case,
            corpus,
            api_key,
            transport,
            previous,
            indexed.get(case.document.path),
            snapshot_filesystem,
        )
        for case in cases
    ]
    rows.extend(
        _review_functions(
            config, functions, api_key, transport, previous, function_repair_round
        )
    )
    report["documents"] = list(rows)
    _summarize(report, rows)
    report["evaluation"] = (
        evaluate(cases, rows, live=api_key is not None, previous=previous)
        if previous is not None
        or any(case.expected_route or case.expected_criteria for case in cases)
        else None
    )
    return report


def _review_case(
    config: ProjectConfig,
    case: Case,
    shared: dict[str, str] | None,
    api_key: str | None,
    transport: Transport,
    previous: dict[str, Json] | None,
    captured_facts: dict[str, Json] | None = None,
    snapshot_filesystem: bool = False,
) -> dict[str, Json]:
    if any(
        reference.path == case.document.path for reference in case.document.references
    ):
        raise ValueError("reference paths must differ from the reviewed document")
    corpus = (
        shared
        if shared is not None
        else {
            case.document.path: case.document.text,
            **{
                reference.path: reference.text for reference in case.document.references
            },
        }
    )
    local_config = (
        config if shared is not None else replace(config, maintained=tuple(corpus))
    )
    if captured_facts is not None:
        if corpus.get(case.document.path) != case.document.text:
            raise ValueError("case text must match the captured repository corpus")
        row = dict(captured_facts)
    else:
        row = report_documents(
            inspect_corpus(
                local_config,
                corpus,
                [case.document.path],
                allow_filesystem=snapshot_filesystem,
            )
        )[0]
    row["case_id"] = case.name
    row["requests"] = list[Json]()
    row["criteria"] = dict[str, Json]()
    if row["hard_failures"]:
        return route_document(row, case, {}, (), previous)
    old_handoff = as_object(row["handoff"], "facts handoff")
    pending = tuple(
        Gap(
            Location(
                case.document.path,
                "Document",
                1,
                max(1, len(case.document.text.splitlines())),
            ),
            str(item),
        )
        for item in json_array(old_handoff["uncertain"])
    )
    executor = Executor(config, api_key, transport)
    facts = as_object(row["facts"], "facts")
    kind, resolution_gaps = _resolve_kind(config, case, row, executor)
    if kind is None:
        criteria = assess_criteria(config, executor)
        return route_document(row, case, criteria, resolution_gaps, previous)
    gaps = [*pending, *resolution_gaps, *_placement(config, case, row, executor)]
    document = replace(case.document, document_type=role_name(kind))
    terms = defined_terms(corpus.get("CONTEXT.md", ""))
    plan = plan_document(
        config, document, kind, facts, corpus=corpus, defined_terms=terms
    )
    gaps.extend(plan.gaps)
    executor.run(plan)
    criteria = assess_criteria(config, executor)
    row["requests"] = list(executor.events)
    row["criteria"] = dict[str, Json](criteria)
    row["coverage_gaps"] = [gap.as_json() for gap in gaps]
    row["not_assessed"] = _remaining_assessments(row, api_key is None, tuple(gaps))
    _check_observations(row, criteria, executor)
    return route_document(row, case, criteria, tuple(gaps), previous)


def _placement(
    config: ProjectConfig, case: Case, row: dict[str, Json], executor: Executor
) -> tuple[Gap, ...]:
    facts = as_object(row["facts"], "facts")
    gaps: list[Gap] = []
    location = Location(
        case.document.path, "Document", 1, max(1, len(case.document.text.splitlines()))
    )
    if facts.get("links_resolve") is None:
        gaps.append(
            Gap(
                location, "link targets outside the supplied snapshot were not assessed"
            )
        )
    if facts.get("placement_ok") is None:
        plan = plan_resolution(
            config, case.document, placement_question(config, location), facts
        )
        executor.run(plan)
        gaps.extend(plan.gaps)
    kind = row.get("type")
    if (
        isinstance(kind, str)
        and config.types[kind].status
        and row.get("status") is None
    ):
        gaps.append(
            Gap(
                location, "inferred lifecycle type needs an explicit status declaration"
            )
        )
    return tuple(gaps)


def _check_observations(
    row: dict[str, Json], criteria: dict[str, dict[str, Json]], executor: Executor
) -> None:
    if executor.api_key is not None and any(
        value.get("family") == "duplication" for value in criteria.values()
    ):
        row["not_assessed"] = [
            value
            for value in json_array(row["not_assessed"])
            if value != "duplication: candidate ownership and content not assessed"
        ]
    if _model_changed(executor) or any(event.get("error") for event in executor.events):
        for criterion in criteria.values():
            criterion["status"] = "uncertain"
        row["not_assessed"] = [
            *json_array(row["not_assessed"]),
            "model revision changed or a required observation failed",
        ]


def _resolve_kind(
    config: ProjectConfig, case: Case, row: dict[str, Json], executor: Executor
) -> tuple[str | None, tuple[Gap, ...]]:
    facts = as_object(row["facts"], "facts")
    kind = row["type"]
    if not isinstance(kind, str) and case.document.document_type is not None:
        kind = (
            "runbook"
            if case.document.document_type == "human_instructions"
            else case.document.document_type
        )
        row.update(type=kind, type_source="input")
    if not isinstance(kind, str):
        location = Location(
            case.document.path,
            "Document",
            1,
            max(1, len(case.document.text.splitlines())),
        )
        resolution = plan_resolution(
            config, case.document, type_question(config, location), facts
        )
        executor.run(resolution)
        resolved = assess_criteria(config, executor)
        type_row = resolved.get("document_type", {})
        if type_row.get("status") != "pass":
            row.update(
                requests=list[Json](executor.events),
                criteria=dict[str, Json](resolved),
                not_assessed=[
                    "type resolution: unconfirmed; rubric questions were not guessed"
                ],
            )
            return None, resolution.gaps
        kind = str(executor.answers["document_type"][0].value)
        row.update(type=kind, type_source="model")
    if kind not in config.types:
        raise ValueError("input type must belong to the configured type set")
    return kind, ()


def _summarize(report: dict[str, Json], rows: list[dict[str, Json]]) -> None:
    requests = [
        as_object(request, "request")
        for row in rows
        for request in json_array(row["requests"])
    ]
    models = sorted(
        {
            str(request["answered_model"])
            for request in requests
            if "answered_model" in request
        }
    )
    report["model"] = {
        "requested": as_object(report["model"], "model")["requested"],
        "answered": models[0] if len(models) == 1 else None,
        "answered_versions": list(models),
    }
    report["summary"] = {
        "documents": len(rows),
        "pass": sum(row["route"] == "pass" for row in rows),
        "revise": sum(row["route"] == "revise" for row in rows),
        "review": sum(row["route"] == "review" for row in rows),
        "hard_failures": sum(len(json_array(row["hard_failures"])) for row in rows),
        "model_failures": sum(bool(request.get("error")) for request in requests),
        "request_count": len(requests),
        "request_strategies": dict(
            Counter(str(request["strategy"]) for request in requests)
        ),
    }


def _remaining_assessments(
    row: dict[str, Json], preview: bool, gaps: tuple[Gap, ...]
) -> list[Json]:
    existing = [
        value
        for value in json_array(row["not_assessed"])
        if not isinstance(value, str) or not value.startswith("model criteria:")
    ]
    if preview:
        existing.append("model criteria: dry-run only; previews are not answers")
    existing.extend(
        f"{gap.location.path}:{gap.location.line_start}: {gap.reason}" for gap in gaps
    )
    return existing


def _model_changed(executor: Executor) -> bool:
    return (
        len(
            {
                event["answered_model"]
                for event in executor.events
                if isinstance(event.get("answered_model"), str)
            }
        )
        > 1
    )


def _review_functions(
    config: ProjectConfig,
    functions: tuple[FunctionDoc, ...],
    api_key: str | None,
    transport: Transport,
    previous: dict[str, Json] | None,
    repair_round: int,
) -> list[dict[str, Json]]:
    groups: dict[str, list[FunctionDoc]] = {}
    for function in functions:
        if (
            function.changed
            and not function.name.rsplit(".", 1)[-1].startswith("_")
            and (function.docstring.strip() or function.required)
        ):
            groups.setdefault(function.path, []).append(function)
    return [
        _function_file(config, path, group, api_key, transport, previous, repair_round)
        for path, group in groups.items()
    ]


def _function_file(
    config: ProjectConfig,
    path: str,
    functions: list[FunctionDoc],
    api_key: str | None,
    transport: Transport,
    previous: dict[str, Json] | None,
    repair_round: int,
) -> dict[str, Json]:
    failures: list[Json] = [
        {
            "rule": failure,
            "line_start": function.line_start,
            "line_end": function.line_end,
            "message": "Provide a useful docstring stating intent or constraints beyond the signature.",
        }
        for function in functions
        if (failure := function.hard_failure()) is not None
    ]
    row: dict[str, Json] = {
        "path": path,
        "case_id": path,
        "type": None,
        "type_source": "python-docstring",
        "status": None,
        "facts": {
            "docstring_facts": [
                {
                    **function.location.as_json(),
                    "docstring_present": bool(function.docstring.strip()),
                    "name_echo": function.hard_failure() == "docstring.name_echo",
                }
                for function in functions
            ]
        },
        "hard_failures": failures,
        "criteria": {},
        "requests": [],
        "not_assessed": ["code behavior: prepared docstring facts only"],
        "route": "revise" if failures else "review",
        "reasons": ["deterministic_requirement_failure"] if failures else [],
        "handoff": {
            "target": "authoring_agent",
            "failed": failures,
            "uncertain": [],
            "located_items": failures,
            "instructions": "Correct the located docstrings within the owning work.",
        },
    }
    if failures:
        return row
    executor = Executor(config, api_key, transport)
    plans = [plan_function(config, function) for function in functions]
    for plan in plans:
        executor.run(plan)
    criteria = assess_criteria(config, executor)
    gaps = tuple(gap for plan in plans for gap in plan.gaps)
    row.update(requests=list[Json](executor.events), criteria=dict[str, Json](criteria))
    if api_key is None:
        row["not_assessed"] = [
            *json_array(row["not_assessed"]),
            "model criteria: dry-run only",
        ]
    _check_observations(row, criteria, executor)
    row["coverage_gaps"] = [gap.as_json() for gap in gaps]
    case = Case(
        path,
        "real",
        Document(path, "", complete=True, context_complete=True),
        repair_round=repair_round,
    )
    return route_document(row, case, criteria, gaps, previous)
