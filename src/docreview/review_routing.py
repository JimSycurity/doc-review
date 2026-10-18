"""Interpret typed observations through explicit action bands, never a composite."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING, override

from docreview.documentation_trial import Thresholds

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig
    from docreview.documentation_decisions import Answer, Json
    from docreview.review_types import Question, Status

SAMPLE_MARGIN = 0.02
SPREAD_LIMIT = 0.10
MIDDLE_FAILURE = 0.80


@dataclass(frozen=True)
class Bands(Thresholds):
    """Use separate action thresholds while keeping the trial's base bands."""

    type_resolution: float = 0.85
    duplication_same: float = 0.80

    def __post_init__(self) -> None:
        super().__post_init__()
        if not all(
            math.isfinite(value) and 0.5 < value <= 1
            for value in (self.type_resolution, self.duplication_same)
        ):
            raise ValueError(
                "resolution and duplication bands must be above .5 through 1"
            )

    @classmethod
    def from_config(cls, config: ProjectConfig) -> Bands:
        return cls(
            config.thresholds["reject_below"],
            config.thresholds["accept_above"],
            config.thresholds["min_confidence"],
            config.thresholds["type_resolution"],
            config.thresholds["duplication_same"],
        )

    @override
    def as_json(self) -> dict[str, Json]:
        return {
            **super().as_json(),
            "type_resolution": self.type_resolution,
            "duplication_same": self.duplication_same,
        }


@dataclass(frozen=True)
class Signals:
    """Coordinate medians are separate signals, not a reconstructed distribution."""

    probabilities: dict[str, float]
    confidence: float
    spread: float

    @classmethod
    def from_answers(cls, answers: tuple[Answer, ...]) -> Signals:
        if not answers:
            raise ValueError("assessment needs an answer")
        keys = answers[0].probabilities.keys()
        values = {
            key: median(answer.probabilities[key] for answer in answers) for key in keys
        }
        spread = max(
            [
                max(answer.probabilities[key] for answer in answers)
                - min(answer.probabilities[key] for answer in answers)
                for key in keys
            ]
            + [
                max(answer.confidence for answer in answers)
                - min(answer.confidence for answer in answers)
            ]
        )
        return cls(values, median(answer.confidence for answer in answers), spread)

    def as_json(self) -> dict[str, Json]:
        return {
            "median_probabilities": dict(self.probabilities),
            "confidence": self.confidence,
            "spread": self.spread,
        }


def needs_repeat(question: Question, answer: Answer, bands: Bands) -> bool:
    """Repeat boundary observations; choice always has its separate reorder check."""
    if question.kind == "choice":
        return False
    boundaries = (
        bands.reject_below,
        bands.accept_above,
        MIDDLE_FAILURE,
        bands.duplication_same,
    )
    return abs(answer.confidence - bands.min_confidence) <= SAMPLE_MARGIN + 1e-9 or any(
        abs(value - boundary) <= SAMPLE_MARGIN + 1e-9
        for value in answer.probabilities.values()
        for boundary in boundaries
    )


def _stable(signals: Signals, bands: Bands) -> bool:
    return (
        signals.confidence >= bands.min_confidence
        and signals.spread <= SPREAD_LIMIT + 1e-9
    )


def _status(question: Question, signals: Signals, bands: Bands) -> tuple[Status, float]:
    values = signals.probabilities
    if question.kind == "noul":
        good = values["true"] if question.positive else values["false"]
        return bands.status(good, 1 - good, signals.confidence), good
    good = values[str(len(question.levels) - 1)]
    if question.family == "plain_english" and values["1"] >= MIDDLE_FAILURE:
        return "fail", good
    return bands.status(good, values["0"], signals.confidence), good


def assess_question(
    question: Question, answers: tuple[Answer, ...], bands: Bands
) -> dict[str, Json]:
    """Unstable samples cannot become author tasks, even with a confident median."""
    signals = Signals.from_answers(answers)
    if question.kind == "choice":
        status, good = _choice(answers, bands)
    else:
        status, good = _status(question, signals, bands)
    if question.kind != "choice" and not _stable(signals, bands):
        status = "uncertain"
    return {
        "question": question.instructions,
        "rubric": question.as_json(),
        "family": question.family,
        "location": question.location.as_json(),
        "status": status,
        "probability_met": good,
        "answer": answers[0].as_json(),
        "sample_answers": [answer.as_json() for answer in answers],
        "decision_signals": signals.as_json(),
    }


def _choice(answers: tuple[Answer, ...], bands: Bands) -> tuple[Status, float]:
    selections = {answer.value for answer in answers}
    good = min(answer.probabilities[str(answer.value)] for answer in answers)
    is_resolved = (
        len(answers) == 2
        and len(selections) == 1
        and not selections & {"unknown", "none"}
        and all(
            answer.confidence >= bands.min_confidence
            and answer.probabilities[str(answer.value)] >= bands.type_resolution
            for answer in answers
        )
    )
    return ("pass" if is_resolved else "uncertain"), good


def assess_duplicate(
    similarity: Question,
    similarities: tuple[Answer, ...],
    ownership: Question,
    ownerships: tuple[Answer, ...],
    bands: Bands,
) -> dict[str, Json]:
    """Never revise from similarity alone or tell the canonical owner to delete text."""
    shared, owner = Signals.from_answers(similarities), Signals.from_answers(ownerships)
    status: Status = "uncertain"
    if _stable(shared, bands):
        if shared.probabilities["0"] >= bands.accept_above:
            status = "pass"
        elif (
            _stable(owner, bands)
            and shared.probabilities["2"] >= bands.duplication_same
            and owner.probabilities["true"] <= bands.reject_below
        ):
            status = "fail"
    return {
        "question": similarity.instructions,
        "ownership_question": ownership.instructions,
        "rubric": similarity.as_json(),
        "ownership_rubric": ownership.as_json(),
        "family": "duplication",
        "location": similarity.location.as_json(),
        "status": status,
        "probability_met": None,
        "answer": similarities[0].as_json(),
        "ownership_answer": ownerships[0].as_json(),
        "sample_answers": [answer.as_json() for answer in similarities],
        "ownership_samples": [answer.as_json() for answer in ownerships],
        "decision_signals": {
            "similarity": shared.as_json(),
            "source_ownership": owner.as_json(),
        },
    }
