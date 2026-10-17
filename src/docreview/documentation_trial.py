"""Route whole-document reviews using typed decisions and document-specific criteria.

Dry-run is the default. Live mode sends selected complete documents to OpenRouter.
Routes are advisory handoffs; the runner never edits documents or dispatches agents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docreview.documentation_decisions import (
    Answer,
    Json,
    as_object,
    decide,
    nonempty_text,
    read_answers,
)
from docreview.documentation_inputs import Case, Document, load_cases
from docreview.documentation_rubrics import TYPES, Criterion, criteria_for

POLICY_VERSION = "documentation-trial-v3.2"
DEFAULT_MODEL = "microsoft/microsoft-decision-1-20261009"
CASE_FILE = Path(__file__).parent / "data/documentation-cases.json"
Status = Literal["pass", "fail", "uncertain"]
Route = Literal["pass", "revise", "review"]


@dataclass(frozen=True)
class Thresholds:
    """Experimental bands; outcome probability and certainty are distinct."""

    reject_below: float = 0.1
    accept_above: float = 0.9
    min_confidence: float = 0.8

    def __post_init__(self) -> None:
        if not 0 <= self.reject_below < 0.5 < self.accept_above <= 1:
            raise ValueError("require 0 <= reject < 0.5 < accept <= 1")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("confidence must be between zero and one")

    def status(self, good: float, bad: float, confidence: float) -> Status:
        """Low certainty always means review, even when the selected result is bad."""
        if confidence < self.min_confidence:
            return "uncertain"
        if good >= self.accept_above:
            return "pass"
        return "fail" if bad >= 1 - self.reject_below else "uncertain"

    def as_json(self) -> dict[str, Json]:
        """Record the actual routing settings in every report."""
        return {
            "reject_below": self.reject_below,
            "accept_above": self.accept_above,
            "min_confidence": self.min_confidence,
        }


def classification_question() -> dict[str, Json]:
    """Use an explicit unknown option rather than force a document into a role."""
    options: dict[str, Json] = dict(TYPES)
    options["unknown"] = (
        "The intended document role cannot be established from the supplied context"
    )
    return {
        "document_type": {
            "type": "choice",
            "instructions": "Which document type fits the intended reader task?",
            "criteria": options,
        }
    }


def questions_for(criteria: dict[str, Criterion]) -> dict[str, Json]:
    """Keep each judgment focused while all questions see the whole document."""
    questions: dict[str, Json] = {}
    for key, criterion in criteria.items():
        question: dict[str, Json] = {
            "type": "score" if criterion.levels else "noul",
            "instructions": criterion.instructions,
        }
        if criterion.levels:
            question["criteria"] = list(criterion.levels)
        questions[key] = question
    return questions


def make_request(
    document: Document,
    model: str,
    questions: dict[str, Json],
    document_type: str | None = None,
    reverse: bool = False,
) -> dict[str, Json]:
    """Preserve all supplied text; there is no paragraph or small byte cutoff."""
    return {
        "model": model,
        "state": document.state(document_type),
        "questions": dict(reversed(list(questions.items()))) if reverse else questions,
    }


def call(
    request: dict[str, Json], api_key: str, events: list[Json]
) -> dict[str, Answer]:
    """Retain request identity, usage and errors without logging private text."""
    started = time.perf_counter()
    event: dict[str, Json] = {
        "request_sha256": hashlib.sha256(json.dumps(request).encode()).hexdigest(),
        "request_bytes": len(json.dumps(request).encode()),
    }
    events.append(event)
    try:
        response = decide(request, api_key)
        event.update(model=response.get("model"), usage=response.get("usage"))
        answers = read_answers(response, as_object(request["questions"], "questions"))
        event["answers"] = {key: answer.as_json() for key, answer in answers.items()}
        return answers
    except ValueError as exc:
        event["error"] = str(exc)
        raise
    finally:
        event["seconds"] = time.perf_counter() - started


def assess(
    criteria: dict[str, Criterion], answers: dict[str, Answer], bands: Thresholds
) -> tuple[dict[str, Json], list[str], list[str], float | None]:
    """Keep individual failures visible; a composite score never changes a route."""
    rows: dict[str, Json] = {}
    failed: list[str] = []
    uncertain: list[str] = []
    weighted = 0.0
    total_weight = 0.0
    for key, criterion in criteria.items():
        answer = answers[key]
        if criterion.levels:
            good = answer.probabilities[str(len(criterion.levels) - 1)]
            bad = answer.probabilities["0"]
            weighted += (
                float(answer.value) / (len(criterion.levels) - 1) * criterion.weight
            )
            total_weight += criterion.weight
        else:
            p = answer.probabilities["true"]
            good = p if criterion.positive else 1 - p
            bad = 1 - good
        status = bands.status(good, bad, answer.confidence)
        rows[key] = {
            "question": criterion.instructions,
            "status": status,
            "probability_met": good,
            "answer": answer.as_json(),
        }
        if status == "fail":
            failed.append(key)
        elif status == "uncertain":
            uncertain.append(key)
    return rows, failed, uncertain, weighted / total_weight if total_weight else None


def choose_route(
    case: Case, failed: list[str], uncertain: list[str]
) -> tuple[Route, list[str]]:
    """Require context before revision/pass; bound author repair handoffs to one round."""
    if not case.document.complete or not case.document.context_complete:
        return "review", ["incomplete_document_or_context"]
    if "context_sufficient" in failed:
        return "review", ["document_role_not_established"]
    if failed:
        return (
            ("review", ["author_repair_budget_exhausted"])
            if case.repair_round >= 1
            else ("revise", ["confident_requirement_failure"])
        )
    return (
        ("review", ["uncertain_requirements"])
        if uncertain
        else ("pass", ["requirements_met"])
    )


def handoff(route: Route, failed: list[str], uncertain: list[str]) -> dict[str, Json]:
    """Provide the caller a next step; this function has no agent side effects."""
    if route == "pass":
        return {
            "target": None,
            "instructions": "The supplied documentation check passes; retain its scope and evidence.",
        }
    target = "authoring_agent" if route == "revise" else "documentation_review_agent"
    instruction = (
        "Inspect the full document and failed criteria. Fix within the owning work and repository authority; resubmit with the repair round incremented."
        if route == "revise"
        else "Inspect the full document, context and criterion results. Resolve missing evidence and ownership; seek the human only for a decision or authority the agents cannot resolve."
    )
    return {
        "target": target,
        "failed_criteria": list(failed),
        "uncertain_criteria": list(uncertain),
        "instructions": instruction,
        "human_decision_required": None,
    }


def review_case(
    case: Case, model: str, api_key: str, bands: Thresholds, reverse: bool = False
) -> dict[str, Json]:
    """Classify if needed, assess the complete document, and emit a bounded handoff."""
    events: list[Json] = []
    result: dict[str, Json] = {
        "id": case.name,
        "path": case.document.path,
        "split": case.split,
        "document_sha256": hashlib.sha256(case.document.text.encode()).hexdigest(),
        "document_bytes": len(case.document.text.encode()),
        "complete": case.document.complete,
        "context_complete": case.document.context_complete,
        "calls": events,
    }
    document_type = case.document.document_type
    failed: list[str] = []
    uncertain: list[str] = []
    try:
        document_type = resolve_type(
            case.document, model, api_key, bands, events, reverse
        )
        if document_type is None:
            route, reasons = "review", ["uncertain_document_type"]
        else:
            criteria = criteria_for(
                document_type,
                bool(case.document.context.strip()),
                bool(case.document.references),
            )
            request = make_request(
                case.document, model, questions_for(criteria), document_type, reverse
            )
            rows, failed, uncertain, composite = assess(
                criteria, call(request, api_key, events), bands
            )
            result.update(criteria=rows, composite_score=composite)
            route, reasons = choose_route(case, failed, uncertain)
    except ValueError as exc:
        result["error"] = str(exc)
        route, reasons = "review", ["model_or_response_error"]
    json_reasons: list[Json] = list(reasons)
    result.update(
        document_type=document_type,
        route=route,
        reasons=json_reasons,
        handoff=handoff(route, failed, uncertain),
    )
    if case.expected_route is not None:
        result.update(
            expected_route=case.expected_route,
            matches_expected=route == case.expected_route,
        )
    return result


def resolve_type(
    document: Document,
    model: str,
    api_key: str,
    bands: Thresholds,
    events: list[Json],
    reverse: bool,
) -> str | None:
    """Honor an established repository role; classify only when it is absent."""
    if document.document_type is not None:
        return document.document_type
    request = make_request(document, model, classification_question(), reverse=reverse)
    answer = call(request, api_key, events)["document_type"]
    selected = str(answer.value)
    good = answer.probabilities[selected]
    return (
        selected
        if selected != "unknown"
        and bands.status(good, 1 - good, answer.confidence) == "pass"
        else None
    )


def run_trial(
    cases: list[Case],
    model: str,
    api_key: str,
    bands: Thresholds,
    reverse: bool = False,
) -> dict[str, Json]:
    """Retain failures as review handoffs and distinguish them from successful calls."""
    rows = [review_case(case, model, api_key, bands, reverse) for case in cases]
    results: list[Json] = list(rows)
    return {
        "policy_version": POLICY_VERSION,
        "requested_model": model,
        "thresholds": bands.as_json(),
        "reverse_questions": reverse,
        "processed_documents": len(results),
        "model_failures": sum("error" in row for row in rows),
        "results": results,
    }


def preview(case: Case, model: str, reverse: bool) -> dict[str, Json]:
    """Preview only the known next request, without predicting classification."""
    kind = case.document.document_type
    questions = (
        questions_for(
            criteria_for(
                kind,
                bool(case.document.context.strip()),
                bool(case.document.references),
            )
        )
        if kind
        else classification_question()
    )
    return make_request(case.document, model, questions, kind, reverse)


def main(argv: list[str] | None = None) -> int:
    """Expose explicit input/model selection without installing hooks or editing files."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: python documentation_trial.py --cases /private/documents.json --split real --model typesafe/jev-1.14 --live --key-file /secure/token > /tmp/documentation-v3.json",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=CASE_FILE,
        help="Explicit whole-document JSON bundle",
    )
    parser.add_argument(
        "--split",
        choices=("all", "development", "holdout", "real"),
        default="development",
        help="Select an evaluation group",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenRouter decision model; choose a context window that fits the whole input",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send selected documents to OpenRouter; default previews only",
    )
    parser.add_argument(
        "--key-file", type=Path, help="Secure token file; otherwise OPENROUTER_API_KEY"
    )
    parser.add_argument(
        "--reverse-questions",
        action="store_true",
        help="Reverse questions, preserving score-level order",
    )
    parser.add_argument(
        "--reject-below",
        type=float,
        default=0.1,
        help="Experimental lower probability-of-compliance band",
    )
    parser.add_argument(
        "--accept-above",
        type=float,
        default=0.9,
        help="Experimental upper probability-of-compliance band",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Experimental certainty required for automatic routing",
    )
    args = parser.parse_args(argv)
    try:
        bands = Thresholds(args.reject_below, args.accept_above, args.min_confidence)
        cases = load_cases(args.cases, args.split)
        model = nonempty_text(args.model, "model")
        if not args.live:
            print(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "requests": [
                            preview(case, model, args.reverse_questions)
                            for case in cases
                        ],
                    },
                    indent=2,
                )
            )
            return 0
        key = (
            args.key_file.read_text().strip()
            if args.key_file
            else os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        if not key:
            raise ValueError("live mode needs a credential source")
        report = run_trial(cases, model, key, bands, args.reverse_questions)
        print(json.dumps(report, indent=2))
        return 2 if report["model_failures"] else 0
    except (ValueError, OSError) as exc:
        print(
            f"Trial could not run ({type(exc).__name__}); check input and credential source",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
