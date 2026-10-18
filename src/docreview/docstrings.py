"""Review prepared changed-function facts without guessing project code ownership."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docreview.configuration import integer, relative_path, table, text
from docreview.documentation_decisions import (
    Json,
    encoded_request,
    nonempty_text,
)
from docreview.review_questions import GUARD
from docreview.review_requests import request_payload
from docreview.review_types import Gap, Location, Plan, Question, Request

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig


@dataclass(frozen=True)
class FunctionDoc:
    """The caller supplies source selection; no shell or repository scan is implied."""

    path: str
    name: str
    signature: str
    docstring: str
    body: str
    line_start: int
    line_end: int
    required: bool = False
    changed: bool = True

    @property
    def location(self) -> Location:
        return Location(self.path, self.name, self.line_start, self.line_end)

    def hard_failure(self) -> str | None:
        if self.required and not self.docstring.strip():
            return "docstring.missing"
        if self.docstring and _tokens(self.docstring) == _tokens(
            self.name.rsplit(".", 1)[-1]
        ):
            return "docstring.name_echo"
        return None


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            r"[a-z0-9]+",
            re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).casefold().replace("_", " "),
        )
    )


def parse_function(value: Json) -> FunctionDoc:
    row = table(
        value,
        {
            "path",
            "name",
            "signature",
            "docstring",
            "body",
            "line_start",
            "line_end",
            "required",
            "changed",
        },
        "function docstring",
    )
    required, changed = row.get("required", False), row.get("changed", True)
    if not isinstance(required, bool) or not isinstance(changed, bool):
        raise TypeError("required and changed must be boolean")
    start, end = integer(row, "line_start", 0), integer(row, "line_end", 0)
    if not 1 <= start <= end:
        raise ValueError("function source range is invalid")
    return FunctionDoc(
        relative_path(nonempty_text(row.get("path"), "function path")),
        nonempty_text(row.get("name"), "function name"),
        nonempty_text(row.get("signature"), "signature"),
        text(row, "docstring"),
        nonempty_text(row.get("body"), "body excerpt"),
        start,
        end,
        required,
        changed,
    )


def plan_function(config: ProjectConfig, function: FunctionDoc) -> Plan:
    question = Question(
        f"docstring_informativeness:{function.name}",
        "docstring_informativeness",
        GUARD
        + "Judge state.signature, state.docstring and state.body for state.name. Does the docstring explain intent, an invariant or a constraint beyond what the signature alone states?",
        function.location,
    )
    state: dict[str, Json] = {
        "path": function.path,
        "name": function.name,
        "signature": function.signature,
        "docstring": function.docstring,
        "body": function.body,
    }
    payload = request_payload(config.model, state, (question,))
    if len(encoded_request(payload)) > config.limits.request_bytes:
        return Plan(
            (),
            (
                Gap(
                    function.location,
                    "request_too_large: complete function input cannot fit",
                ),
            ),
        )
    return Plan((Request(payload, (question,), "docstring"),))
