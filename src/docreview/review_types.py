"""Typed review questions, locations and bounded request plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from docreview.documentation_decisions import Json

Status = Literal["pass", "fail", "uncertain"]
Route = Literal["pass", "revise", "review"]


@dataclass(frozen=True)
class Location:
    """Keep stable heading identity alongside the current source range."""

    path: str
    heading: str
    line_start: int
    line_end: int

    def as_json(self) -> dict[str, Json]:
        return {
            "path": self.path,
            "heading": self.heading,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True)
class Question:
    """A single condition and its local routing interpretation."""

    key: str
    family: str
    instructions: str
    location: Location
    kind: Literal["noul", "score", "choice"] = "noul"
    levels: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    positive: bool = True
    group: str = ""

    def as_json(self, *, reverse: bool = False) -> dict[str, Json]:
        result: dict[str, Json] = {"type": self.kind, "instructions": self.instructions}
        if self.kind == "score":
            result["criteria"] = list(self.levels)
        elif self.kind == "choice":
            result["criteria"] = dict(
                reversed(self.options) if reverse else self.options
            )
        return result


@dataclass(frozen=True)
class Request:
    """The preview and the actual provider call share exactly this payload."""

    payload: dict[str, Json]
    questions: tuple[Question, ...]
    strategy: str


@dataclass(frozen=True)
class Gap:
    """An input that cannot be assessed is never silently omitted or truncated."""

    location: Location
    reason: str

    def as_json(self) -> dict[str, Json]:
        return {"location": self.location.as_json(), "reason": self.reason}


@dataclass(frozen=True)
class Plan:
    requests: tuple[Request, ...]
    gaps: tuple[Gap, ...] = ()
