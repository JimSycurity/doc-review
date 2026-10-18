"""Build complete document inputs and retain diff context without truncation."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from docreview.configuration import relative_path
from docreview.documentation_decisions import Json, as_object, nonempty_text
from docreview.documentation_rubrics import TYPES


@dataclass(frozen=True)
class Reference:
    """Explicit comparison material selected by the review caller."""

    path: str
    text: str

    def as_json(self) -> dict[str, Json]:
        """Keep source identity alongside its content."""
        return {"path": self.path, "text": self.text}


@dataclass(frozen=True)
class Document:
    """A whole resulting document and the context needed to assess it."""

    path: str
    text: str
    purpose: str = ""
    audience: str = ""
    context: str = ""
    references: tuple[Reference, ...] = ()
    diff: str = ""
    document_type: str | None = None
    complete: bool = True
    context_complete: bool = False

    def state(self, document_type: str | None = None) -> dict[str, Json]:
        """Send complete text and supporting context, not case/evaluation labels."""
        return {
            "path": self.path,
            "document": self.text,
            "purpose": self.purpose,
            "audience": self.audience,
            "repository_context": self.context,
            "references": [reference.as_json() for reference in self.references],
            "diff": self.diff,
            "intended_type": document_type,
        }


@dataclass(frozen=True)
class Case:
    """A selected document; expectations and retry count remain local."""

    name: str
    split: str
    document: Document
    repair_round: int = 0
    expected_route: str | None = None
    expected_criteria: dict[str, str] = field(default_factory=dict)


def from_snapshots(
    path: str,
    before: str,
    after: str,
    *,
    purpose: str = "",
    audience: str = "",
    context: str = "",
    references: tuple[Reference, ...] = (),
    document_type: str | None = None,
    context_complete: bool = False,
) -> Document:
    """Preserve the full after text; the complete diff supplies attribution only."""
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path + " (before)",
            tofile=path + " (after)",
        )
    )
    return Document(
        path,
        after,
        purpose,
        audience,
        context,
        references,
        diff,
        document_type,
        True,
        context_complete,
    )


def _text(row: dict[str, Json], key: str) -> str:
    value = row.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def _boolean(row: dict[str, Json], key: str, default: bool) -> bool:
    value = row.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def parse_document(value: Json, allowed_types: set[str] | None = None) -> Document:
    """Validate the explicit input, leaving all supplied text intact."""
    row = as_object(value, "document")
    document_type = row.get("document_type")
    if document_type is not None and (
        not isinstance(document_type, str)
        or document_type not in (set(TYPES) | (allowed_types or set()))
    ):
        raise ValueError("unknown document_type")
    raw_references = row.get("references", [])
    if not isinstance(raw_references, list):
        raise ValueError("references must be a list")
    references: list[Reference] = []
    for raw in raw_references:
        item = as_object(raw, "reference")
        references.append(
            Reference(
                relative_path(nonempty_text(item.get("path"), "reference path")),
                nonempty_text(item.get("text"), "reference text"),
            )
        )
    if len({reference.path for reference in references}) != len(references):
        raise ValueError("reference paths must be unique")
    return Document(
        relative_path(nonempty_text(row.get("path"), "path")),
        nonempty_text(row.get("text"), "text"),
        _text(row, "purpose"),
        _text(row, "audience"),
        _text(row, "context"),
        tuple(references),
        _text(row, "diff"),
        document_type,
        _boolean(row, "complete", True),
        _boolean(row, "context_complete", False),
    )


def parse_case(value: Json, allowed_types: set[str] | None = None) -> Case:
    """Require valid routing/evaluation metadata without putting it in model state."""
    row = as_object(value, "case")
    split = nonempty_text(row.get("split"), "split")
    if split not in {"development", "holdout", "real"}:
        raise ValueError("unsupported split")
    repair_round = row.get("repair_round", 0)
    if (
        isinstance(repair_round, bool)
        or not isinstance(repair_round, int)
        or repair_round < 0
    ):
        raise ValueError("repair_round must be a nonnegative integer")
    expected = row.get("expected_route")
    if expected is not None and (
        not isinstance(expected, str) or expected not in {"pass", "revise", "review"}
    ):
        raise ValueError("unsupported expected_route")
    return Case(
        nonempty_text(row.get("id"), "id"),
        split,
        parse_document(row.get("document"), allowed_types),
        repair_round,
        expected,
        _expected_criteria(row.get("expected_criteria", {})),
    )


def load_cases(
    path: Path, split: str, allowed_types: set[str] | None = None
) -> list[Case]:
    """Read only the selected bundle; never discover documents or credentials."""
    from docreview.documentation_decisions import read_json

    raw = read_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("cases must be a nonempty list")
    cases = [parse_case(value, allowed_types) for value in raw]
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    selected = [case for case in cases if split == "all" or case.split == split]
    if not selected:
        raise ValueError("no cases match the split")
    return selected


def _expected_criteria(value: Json) -> dict[str, str]:
    row = as_object(value, "expected_criteria")
    result = {}
    for key, status in row.items():
        if not isinstance(status, str) or status not in {"pass", "fail", "uncertain"}:
            raise ValueError(
                "expected criterion status must be pass, fail or uncertain"
            )
        result[key] = status
    return result
