"""Execute bounded plans or preview them; retain every independent observation."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from docreview.documentation_decisions import (
    Answer,
    Json,
    as_object,
    decide,
    encoded_request,
    read_answers,
)
from docreview.review_routing import Bands, needs_repeat
from docreview.review_types import Plan, Question, Request

if TYPE_CHECKING:
    from collections.abc import Callable

    from docreview.configuration import ProjectConfig

type Transport = Callable[[dict[str, Json], str], dict[str, Json]]


@dataclass
class Executor:
    """One document's observations; never edits source or dispatches another agent."""

    config: ProjectConfig
    api_key: str | None = None
    transport: Transport = decide
    events: list[dict[str, Json]] = field(default_factory=list)
    questions: dict[str, Question] = field(default_factory=dict)
    answers: dict[str, list[Answer]] = field(default_factory=dict)

    def run(self, plan: Plan) -> None:
        for request in plan.requests:
            self.questions.update(
                (question.key, question) for question in request.questions
            )
            self._request(request)

    def _request(self, request: Request) -> None:
        self._call(request)
        choices = tuple(
            question for question in request.questions if question.kind == "choice"
        )
        if choices:
            self._call(_variant(request, choices, reverse=True))
        if self.api_key is None:
            return
        bands = Bands.from_config(self.config)
        borderline = tuple(
            question
            for question in request.questions
            if question.key in self.answers
            and needs_repeat(question, self.answers[question.key][0], bands)
        )
        if borderline:
            for _ in range(2):
                self._call(_variant(request, borderline))

    def _call(self, request: Request) -> None:
        payload = {
            **request.payload,
            "state": {
                **as_object(request.payload["state"], "state"),
                "nonce": str(uuid4()),
            },
        }
        encoded = encoded_request(payload)
        if len(encoded) > self.config.limits.request_bytes:
            raise ValueError("planned request exceeds the declared byte limit")
        event: dict[str, Json] = {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "strategy": request.strategy,
            "model": self.config.model,
            "usage": {},
            "seconds": 0.0,
            "error": None,
        }
        self.events.append(event)
        if self.api_key is None:
            event["preview"] = payload
            return
        started = time.perf_counter()
        try:
            response = self.transport(payload, self.api_key)
            model = response.get("model")
            if not isinstance(model, str) or not (
                model == self.config.model or model.startswith(self.config.model + "-")
            ):
                raise ValueError("response model does not match the pinned selection")
            answers = read_answers(
                response, as_object(payload["questions"], "questions")
            )
            event.update(
                answered_model=model,
                usage=_usage(response.get("usage")),
                answers=dict[str, Json](
                    (key, answer.as_json()) for key, answer in answers.items()
                ),
            )
            for key, answer in answers.items():
                self.answers.setdefault(key, []).append(answer)
        except (ValueError, OSError) as exc:
            event["error"] = str(exc)
        finally:
            event["seconds"] = time.perf_counter() - started


def _variant(
    request: Request, questions: tuple[Question, ...], *, reverse: bool = False
) -> Request:
    return Request(
        {
            **request.payload,
            "questions": {
                question.key: question.as_json(reverse=reverse)
                for question in questions
            },
        },
        questions,
        request.strategy,
    )


def _usage(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in {"input_tokens", "output_tokens", "cost"}
        and not isinstance(item, bool)
        and isinstance(item, (int, float))
    }
