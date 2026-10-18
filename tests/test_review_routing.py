"""Test routing bands independently of live model quality."""

import pytest

from docreview.documentation_decisions import Answer
from docreview.documentation_rubrics import PLAIN_ENGLISH
from docreview.review_routing import Bands, assess_duplicate, assess_question
from docreview.review_types import Location, Question

PLACE = Location("docs/usage/recover.md", "Recovery", 5, 12)


def score(p0: float, p1: float, p2: float, confidence: float = 1) -> Answer:
    return Answer("score", p1 + 2 * p2, {"0": p0, "1": p1, "2": p2}, confidence)


def noul(probability: float) -> Answer:
    return Answer(
        "noul",
        probability,
        {"true": probability, "false": 1 - probability},
        abs(2 * probability - 1),
    )


def test_middle_plain_english_is_a_located_failure() -> None:
    question = Question(
        "plain_english:recovery",
        "plain_english",
        "Judge the recovery text.",
        PLACE,
        "score",
        PLAIN_ENGLISH.levels,
    )
    row = assess_question(question, (score(0.01, 0.98, 0.01),), Bands())
    assert row["status"] == "fail"
    assert row["location"] == PLACE.as_json()


def test_duplication_requires_same_guidance_and_negative_source_ownership() -> None:
    similarity = Question(
        "similarity",
        "duplicate_similarity",
        "Compare the sections.",
        PLACE,
        "score",
        ("distinct", "overlap", "same"),
        group="pair",
    )
    ownership = Question(
        "ownership",
        "duplicate_ownership",
        "Is the changed section the canonical home?",
        PLACE,
        group="pair",
    )
    row = assess_duplicate(
        similarity, (score(0, 0, 1),), ownership, (noul(0.01),), Bands()
    )
    assert row["status"] == "fail"
    row = assess_duplicate(
        similarity, (score(0, 0, 1),), ownership, (noul(0.99),), Bands()
    )
    assert row["status"] == "uncertain"


def test_distinct_guidance_does_not_require_an_ownership_decision() -> None:
    similarity = Question(
        "similarity",
        "duplicate_similarity",
        "Compare.",
        PLACE,
        "score",
        ("distinct", "overlap", "same"),
        group="pair",
    )
    ownership = Question(
        "ownership", "duplicate_ownership", "Canonical home?", PLACE, group="pair"
    )
    row = assess_duplicate(
        similarity, (score(1, 0, 0),), ownership, (noul(0.5),), Bands()
    )
    assert row["status"] == "pass"


def test_choice_resolution_requires_agreement_not_matching_probability_mass() -> None:
    question = Question(
        "type",
        "type_resolution",
        "Choose type.",
        PLACE,
        "choice",
        options=(("readme", "Project start"), ("unknown", "Unknown")),
    )
    answers = (
        Answer("choice", "readme", {"readme": 0.99, "unknown": 0.01}, 1),
        Answer("choice", "readme", {"readme": 0.85, "unknown": 0.15}, 1),
    )
    assert assess_question(question, answers, Bands())["status"] == "pass"


@pytest.mark.parametrize(
    ("probability", "status"),
    [(0.01, "fail"), (0.5, "uncertain"), (0.99, "pass"), (0.9, "pass"), (0.1, "fail")],
)
def test_noul_action_bands(probability: float, status: str) -> None:
    question = Question("scope", "scope", "Does the section fit?", PLACE)
    assert assess_question(question, (noul(probability),), Bands())["status"] == status


def test_score_confidence_and_sample_spread_block_automatic_actions() -> None:
    question = Question(
        "english",
        "plain_english",
        "Judge clarity.",
        PLACE,
        "score",
        PLAIN_ENGLISH.levels,
    )
    assert (
        assess_question(question, (score(0, 0, 1, 0.79),), Bands())["status"]
        == "uncertain"
    )
    assert (
        assess_question(
            question, (score(0, 0, 1), score(0.2, 0, 0.8), score(0, 0, 1)), Bands()
        )["status"]
        == "uncertain"
    )
    assert assess_question(question, (score(1, 0, 0),), Bands())["status"] == "fail"
    assert (
        assess_question(question, (score(0.01, 0.79, 0.2),), Bands())["status"]
        == "uncertain"
    )


def test_choice_requires_both_probability_and_confidence() -> None:
    question = Question(
        "type",
        "type_resolution",
        "Choose.",
        PLACE,
        "choice",
        options=(("readme", "Start"), ("unknown", "Unknown")),
    )
    good = Answer("choice", "readme", {"readme": 0.99, "unknown": 0.01}, 1)
    weak = Answer("choice", "readme", {"readme": 0.84, "unknown": 0.16}, 1)
    unsure = Answer("choice", "readme", {"readme": 0.99, "unknown": 0.01}, 0.79)
    assert assess_question(question, (good,), Bands())["status"] == "uncertain"
    assert assess_question(question, (good, weak), Bands())["status"] == "uncertain"
    assert assess_question(question, (good, unsure), Bands())["status"] == "uncertain"


def test_duplication_middle_and_low_ownership_confidence_stay_review() -> None:
    similarity = Question(
        "s",
        "duplicate_similarity",
        "Compare.",
        PLACE,
        "score",
        ("distinct", "overlap", "same"),
    )
    ownership = Question("o", "duplicate_ownership", "Canonical?", PLACE)
    assert (
        assess_duplicate(
            similarity, (score(0, 1, 0),), ownership, (noul(0.01),), Bands()
        )["status"]
        == "uncertain"
    )
    assert (
        assess_duplicate(
            similarity, (score(0, 0, 1),), ownership, (noul(0.5),), Bands()
        )["status"]
        == "uncertain"
    )
