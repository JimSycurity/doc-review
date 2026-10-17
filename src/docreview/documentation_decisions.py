"""Typed OpenRouter Decisions boundary; no document policy or workflow actions."""

from __future__ import annotations

import http.client
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None


def read_json(text: str) -> Json:
    """Decode JSON at the sole unchecked stdlib decoder boundary."""
    return cast("Json", json.loads(text))


def as_object(value: Json, name: str) -> dict[str, Json]:
    """Require an object at a named input boundary."""
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def nonempty_text(value: Json, name: str) -> str:
    """Require nonempty text without coercing other types."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def decide(request: Mapping[str, Json], api_key: str) -> dict[str, Json]:
    """Call only OpenRouter Decisions; withhold raw provider errors and secrets."""
    if not api_key or any(character.isspace() for character in api_key):
        raise ValueError("credential must be a single nonempty token")
    payload = json.dumps(request)
    if api_key in payload:
        raise ValueError("authentication material must not appear in document state")
    connection = http.client.HTTPSConnection("openrouter.ai", timeout=30)
    try:
        connection.request(
            "POST",
            "/api/alpha/decisions",
            payload.encode(),
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(
                f"Decision API returned HTTP {response.status}; response body withheld"
            )
        content = response.read(1_000_001)
        if len(content) > 1_000_000:
            raise ValueError("Decision response exceeds response size limit")
        return as_object(read_json(content.decode("utf-8")), "response")
    except (OSError, http.client.HTTPException, UnicodeError) as exc:
        raise ValueError(
            f"Decision transport failed ({type(exc).__name__}); details withheld"
        ) from None
    finally:
        connection.close()


def probability(value: Json) -> float:
    """Require an actual finite number from zero through one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be numeric")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("probability must be finite and between zero and one")
    return float(value)


@dataclass(frozen=True)
class Answer:
    """A validated decision, including its outcome and separate certainty."""

    kind: str
    value: str | float
    probabilities: dict[str, float]
    confidence: float

    def as_json(self) -> dict[str, Json]:
        """Preserve the whole distribution for review and repeat comparisons."""
        return {
            "type": self.kind,
            "value": self.value,
            "probabilities": dict(self.probabilities),
            "confidence": self.confidence,
        }


def distribution(value: Json, labels: set[str]) -> dict[str, float]:
    """Require exactly the requested outcomes and a valid probability mass."""
    raw = as_object(value, "probabilities")
    if set(raw) != labels:
        raise ValueError("probability labels do not match the question")
    values = {key: probability(item) for key, item in raw.items()}
    if not math.isclose(sum(values.values()), 1, abs_tol=0.001):
        raise ValueError("probabilities do not sum to one")
    return values


def read_score(raw: dict[str, Json], levels: list[Json]) -> Answer:
    """Check the declared score against its ordered distribution."""
    expected_legend: dict[str, Json] = {str(i): text for i, text in enumerate(levels)}
    if "legend" in raw and raw["legend"] != expected_legend:
        raise ValueError("score legend does not match the question")
    values = distribution(raw.get("probabilities"), set(expected_legend))
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be numeric")
    mean = sum(int(key) * value for key, value in values.items())
    if (
        not math.isfinite(score)
        or not 0 <= score <= len(levels) - 1
        or not math.isclose(score, mean, abs_tol=0.02)
    ):
        raise ValueError("score does not match its distribution")
    return Answer("score", float(score), values, probability(raw.get("confidence")))


def read_answer(value: Json, question: dict[str, Json]) -> Answer:
    """Validate the requested primitive; never substitute a missing answer."""
    raw = as_object(value, "answer")
    kind = question["type"]
    if raw.get("type") != kind:
        raise ValueError("answer primitive does not match the question")
    if kind == "noul":
        p = probability(raw.get("noul"))
        return Answer("noul", p, {"true": p, "false": 1 - p}, abs(2 * p - 1))
    if kind == "score":
        levels = question.get("criteria")
        if not isinstance(levels, list):
            raise ValueError("score levels are missing")
        return read_score(raw, levels)
    options = as_object(question.get("criteria"), "choice options")
    values = distribution(raw.get("probabilities"), set(options))
    choice = nonempty_text(raw.get("choice"), "choice")
    if choice not in values or values[choice] < max(values.values()):
        raise ValueError("choice is not a highest-probability option")
    return Answer("choice", choice, values, probability(raw.get("confidence")))


def read_answers(
    response: Mapping[str, Json], questions: dict[str, Json]
) -> dict[str, Answer]:
    """Require all and only the requested decisions from a named model."""
    nonempty_text(response.get("model"), "response model")
    raw = as_object(response.get("answers"), "answers")
    if set(raw) != set(questions):
        raise ValueError("response question IDs do not match the request")
    return {
        key: read_answer(value, as_object(questions[key], "question"))
        for key, value in raw.items()
    }
