"""Build whole-document or section-bundle requests without cutting source text."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import TYPE_CHECKING

from docreview.configuration import integer
from docreview.configuration import text as scalar_text
from docreview.documentation_decisions import Json, as_object, encoded_request
from docreview.frontmatter import validate_document
from docreview.markdown import sections
from docreview.review_questions import (
    audience_for,
    document_questions,
    duplicate_questions,
    purpose_for,
    section_questions,
)
from docreview.review_types import Gap, Location, Plan, Question, Request

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docreview.configuration import ProjectConfig
    from docreview.documentation_inputs import Document

NONCE = "00000000-0000-0000-0000-000000000000"


def request_payload(
    model: str,
    state: dict[str, Json],
    questions: tuple[Question, ...],
    *,
    reverse: bool = False,
) -> dict[str, Json]:
    return {
        "model": model,
        "state": {**state, "nonce": NONCE},
        "questions": {
            question.key: question.as_json(reverse=reverse) for question in questions
        },
    }


def _section_states(document: Document, start: int) -> dict[str, dict[str, Json]]:
    lines = document.text.splitlines(keepends=True)
    counts: Counter[str] = Counter()
    result = {}
    for section in sections(document.text, start):
        slug = re.sub(r"\W+", "-", section.heading.casefold()).strip("-") or "opening"
        counts[slug] += 1
        key = slug if counts[slug] == 1 else f"{slug}-{counts[slug]}"
        result[key] = {
            **Location(
                document.path, section.heading, section.line_start, section.line_end
            ).as_json(),
            "words": section.words,
            "text": "".join(lines[section.line_start - 1 : section.line_end]),
        }
    return result


def _outline(states: dict[str, dict[str, Json]]) -> list[Json]:
    """The document path is stated once; outline entries carry only navigation."""
    return [
        {
            key: value
            for key, value in section.items()
            if key not in {"text", "path", "readability"}
        }
        for section in states.values()
    ]


def _with_readability(
    states: dict[str, dict[str, Json]], facts: dict[str, Json]
) -> dict[str, dict[str, Json]]:
    """Attach each section's own readability signal instead of a document-wide table."""
    values = facts.get("readability", [])
    if not isinstance(values, list):
        raise TypeError("readability facts must be a list")
    by_line = {
        integer(as_object(value, "readability"), "line_start", 0): {
            key: item
            for key, item in as_object(value, "readability").items()
            if key in {"words_per_sentence", "long_word_ratio"}
        }
        for value in values
    }
    return {
        key: (
            {**state, "readability": by_line[line]}
            if (line := integer(state, "line_start", 0)) in by_line
            else state
        )
        for key, state in states.items()
    }


def _location(value: dict[str, Json]) -> Location:
    return Location(
        scalar_text(value, "path"),
        scalar_text(value, "heading"),
        integer(value, "line_start", 1),
        integer(value, "line_end", 1),
    )


def _state(
    config: ProjectConfig,
    document: Document,
    kind: str,
    section_states: dict[str, dict[str, Json]],
    facts: dict[str, Json],
    defined_terms: tuple[str, ...],
) -> dict[str, Json]:
    # Repository context is sent only to the ownership questions that read it.
    return {
        "path": document.path,
        "document_type": kind,
        "purpose": purpose_for(config, document, kind),
        "audience": audience_for(config, document, kind),
        "diff": document.diff,
        "defined_terms": list(defined_terms),
        "index_entries": facts.get("index_entries", []),
        "outline": _outline(section_states),
        "opening": next(iter(section_states), ""),
        "sections": {},
        "coverage": "section_bundle",
    }


def _pack(
    config: ProjectConfig,
    base: dict[str, Json],
    units: list[tuple[dict[str, Json], tuple[Question, ...]]],
    strategy: str,
) -> Plan:
    requests = []
    gaps = []
    state = dict(base)
    questions: tuple[Question, ...] = ()
    for addition, new_questions in units:
        candidate_state = {
            **state,
            "sections": {
                **as_object(state.get("sections", {}), "sections"),
                **addition,
            },
        }
        candidate_questions = (*questions, *new_questions)
        payload = request_payload(config.model, candidate_state, candidate_questions)
        if len(encoded_request(payload)) <= config.limits.request_bytes:
            state, questions = candidate_state, candidate_questions
            continue
        if questions:
            requests.append(
                Request(
                    request_payload(config.model, state, questions), questions, strategy
                )
            )
        state = {
            **base,
            "sections": {**as_object(base.get("sections", {}), "sections"), **addition},
        }
        questions = new_questions
        if (
            len(encoded_request(request_payload(config.model, state, questions)))
            > config.limits.request_bytes
        ):
            gaps.extend(
                Gap(
                    question.location,
                    f"request_too_large: {question.family}: opening, outline and complete target cannot fit the request budget",
                )
                for question in questions
            )
            state, questions = dict(base), ()
    if questions:
        requests.append(
            Request(
                request_payload(config.model, state, questions), questions, strategy
            )
        )
    return Plan(tuple(requests), tuple(gaps))


def plan_document(
    config: ProjectConfig,
    document: Document,
    kind: str,
    facts: dict[str, Json],
    *,
    corpus: Mapping[str, str] | None = None,
    defined_terms: tuple[str, ...] = (),
) -> Plan:
    """Each section is complete, even when the whole document needs several calls."""
    declaration = validate_document(config, document.path, document.text)
    states = _with_readability(_section_states(document, declaration.body_start), facts)
    base = _state(config, document, kind, states, facts, defined_terms)
    location = Location(
        document.path, "Document", 1, max(1, len(document.text.splitlines()))
    )
    purpose, audience = (
        purpose_for(config, document, kind),
        audience_for(config, document, kind),
    )
    common = document_questions(
        kind, purpose, audience, location, rubric=config.types[kind].rubric
    )
    signaled = _signal_lines(facts)
    units: list[tuple[dict[str, Json], tuple[Question, ...]]] = [
        ({}, (question,)) for question in common
    ]
    for key, value in states.items():
        target = _location(value)
        units.append(
            (
                {key: value},
                section_questions(
                    kind,
                    purpose,
                    audience,
                    key,
                    target,
                    has_execution_signal=target.line_start in signaled,
                    term_usages=_term_usages(facts, target),
                ),
            )
        )
    all_questions = tuple(question for _, questions in units for question in questions)
    whole_sections: dict[str, Json] = dict(states)
    whole: dict[str, Json] = {
        **base,
        "sections": whole_sections,
        "coverage": "whole",
    }
    payload = request_payload(config.model, whole, all_questions)
    if len(encoded_request(payload)) <= config.limits.request_bytes:
        plan = Plan((Request(payload, all_questions, "whole"),))
    else:
        opening = next(iter(states))
        plan = _pack(
            config,
            {**base, "sections": {opening: states[opening]}},
            units,
            "section_bundle",
        )
    duplicate_plan = _duplicates(config, document, base, facts, corpus or {})
    return Plan(
        (*plan.requests, *duplicate_plan.requests), (*plan.gaps, *duplicate_plan.gaps)
    )


def _term_usages(
    facts: dict[str, Json], target: Location
) -> tuple[tuple[str, str], ...]:
    """Concept-scoped term hits inside one section; uncategorized hits stay facts."""
    values = facts.get("term_usage", [])
    if not isinstance(values, list):
        raise TypeError("term usage facts must be a list")
    found: dict[tuple[str, str], None] = {}
    for value in values:
        row = as_object(value, "term usage")
        concept = row.get("concept")
        line = integer(row, "line_start", 0)
        if isinstance(concept, str) and target.line_start <= line <= target.line_end:
            found[(scalar_text(row, "term"), concept)] = None
    return tuple(found)


def _signal_lines(facts: dict[str, Json]) -> set[int]:
    values = facts.get("execution_log_signals", [])
    if not isinstance(values, list):
        raise TypeError("execution signals must be a list")
    return {integer(as_object(value, "signal"), "line_start", 0) for value in values}


def _duplicates(
    config: ProjectConfig,
    document: Document,
    base: dict[str, Json],
    facts: dict[str, Json],
    corpus: Mapping[str, str],
) -> Plan:
    values = facts.get("duplicate_candidates", [])
    if not isinstance(values, list):
        raise TypeError("duplicate candidates must be a list")
    requests: list[Request] = []
    gaps: list[Gap] = []
    for raw in values:
        pair = as_object(raw, "candidate")
        left, right = (
            as_object(pair.get("left"), "left"),
            as_object(pair.get("right"), "right"),
        )
        source, reference = (
            (left, right) if left.get("path") == document.path else (right, left)
        )
        location = _location(source)
        pair_id = _pair_identity(source, reference, corpus)
        questions = duplicate_questions(pair_id, location)
        if scalar_text(reference, "path") not in corpus:
            gaps.append(Gap(location, "duplicate_reference_missing"))
            continue
        state: dict[str, Json] = {
            "path": base["path"],
            "document_type": base["document_type"],
            "purpose": base["purpose"],
            "context": document.context,
            "index_entries": base["index_entries"],
            "pairs": {
                pair_id: {
                    "changed": _excerpt(source, corpus),
                    "reference": _excerpt(reference, corpus),
                }
            },
        }
        payload = request_payload(config.model, state, questions)
        if len(encoded_request(payload)) <= config.limits.request_bytes:
            requests.append(Request(payload, questions, "reference_pair"))
        else:
            gaps.append(
                Gap(location, "request_too_large: complete reference pair cannot fit")
            )
    return Plan(tuple(requests), tuple(gaps))


def _pair_identity(
    source: dict[str, Json], reference: dict[str, Json], corpus: Mapping[str, str]
) -> str:
    identities = []
    for value in (source, reference):
        place = _location(value)
        occurrence = sum(
            section.heading == place.heading and section.line_start <= place.line_start
            for section in sections(corpus.get(place.path, ""))
        )
        identities.append(f"{place.path}\0{place.heading}\0{occurrence}")
    return hashlib.sha256("\0".join(identities).encode()).hexdigest()[:16]


def _excerpt(location: dict[str, Json], corpus: Mapping[str, str]) -> dict[str, Json]:
    place = _location(location)
    text = corpus[place.path]
    return {
        **place.as_json(),
        "text": "".join(
            text.splitlines(keepends=True)[place.line_start - 1 : place.line_end]
        ),
    }


def plan_resolution(
    config: ProjectConfig,
    document: Document,
    question: Question,
    facts: dict[str, Json],
) -> Plan:
    """Type/placement choices see the opening and outline, without an invented role."""
    states = _section_states(
        document, validate_document(config, document.path, document.text).body_start
    )
    first = next(iter(states))
    state: dict[str, Json] = {
        "path": document.path,
        "context": document.context,
        "diff": document.diff,
        "outline": _outline(states),
        "opening": first,
        "sections": {first: states[first]},
        "index_entries": facts.get("index_entries", []),
    }
    payload = request_payload(config.model, state, (question,))
    if (
        len(question.options) > 255
        or len(encoded_request(payload)) > config.limits.request_bytes
    ):
        return Plan(
            (),
            (
                Gap(
                    question.location, "resolution input cannot fit the provider limits"
                ),
            ),
        )
    return Plan((Request(payload, (question,), "classification"),))
