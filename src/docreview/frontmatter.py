"""Validate only the two scalar keys; this is intentionally not a YAML parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from docreview.markdown import prose_lines
from docreview.paths import matches

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig
    from docreview.documentation_decisions import Json

REJECTED_KEYS = {
    **dict.fromkeys(("title",), "the first heading owns the title"),
    **dict.fromkeys(
        ("description", "summary", "read_when", "purpose"),
        "the governing index owns the one-line routing text",
    ),
    **dict.fromkeys(
        ("date", "created", "updated", "last_reviewed"),
        "Git history owns dates; a hand-maintained date is wrong within weeks",
    ),
    **dict.fromkeys(
        ("author", "owner", "maintainer"), "Git and code ownership own attribution"
    ),
    **dict.fromkeys(
        ("tags", "keywords", "category", "topics"),
        "no consumer reads them; type is the only category",
    ),
    **dict.fromkeys(("version",), "nothing increments it"),
    **dict.fromkeys(
        ("related", "links", "see_also"),
        "body links own relationships, and the link graph is computed",
    ),
    **dict.fromkeys(("id", "slug", "uid"), "the path is the identifier"),
    **dict.fromkeys(
        ("draft", "wip", "archived", "deprecated"),
        "status owns lifecycle; booleans fork it",
    ),
}


# Status labels seen in practice: **Status:**, **Status**: and a plain Status: line.
INLINE_STATUS = re.compile(r"\s*(?:\*\*Status:\*\*|\*\*Status\*\*:|Status:)")


@dataclass(frozen=True)
class Finding:
    """One located rule violation; messages contain no source or credential dump."""

    rule: str
    line_start: int
    line_end: int
    message: str

    def as_json(self) -> dict[str, Json]:
        return {
            "rule": self.rule,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "message": self.message,
        }


@dataclass(frozen=True)
class Declaration:
    """Keep resolved metadata separate from violations and ownership decisions."""

    document_type: str | None
    type_source: str | None
    status: str | None
    body_start: int
    failures: tuple[Finding, ...] = ()
    review_items: tuple[Finding, ...] = ()


def _parse(text: str) -> tuple[dict[str, tuple[str, int]], int, list[Finding]]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, 1, []
    values: dict[str, tuple[str, int]] = {}
    failures: list[Finding] = []
    for number, line in enumerate(lines[1:], 2):
        if line == "---":
            return values, number + 1, failures
        if not line.strip():
            continue
        _parse_scalar(values, failures, number, line)
    failures.append(
        Finding(
            "frontmatter.unclosed",
            1,
            len(lines),
            "frontmatter needs a closing --- line",
        )
    )
    return values, len(lines) + 1, failures


def _parse_scalar(
    values: dict[str, tuple[str, int]], failures: list[Finding], number: int, line: str
) -> None:
    match = re.fullmatch(r"([a-z_]+):[ \t]*(.*?)\s*", line)
    if not match:
        failures.append(
            Finding(
                "frontmatter.scalar",
                number,
                number,
                "use plain key: value scalars only; no nesting, lists, anchors or multiline values",
            )
        )
        return
    key, value = match.groups()
    if key in values:
        failures.append(
            Finding(
                "frontmatter.duplicate_key",
                number,
                number,
                f"frontmatter key '{key}' appears twice",
            )
        )
    if key not in {"type", "status"}:
        reason = REJECTED_KEYS.get(
            key,
            "not in the contract; type identifies kind and status identifies lifecycle",
        )
        failures.append(
            Finding(
                "frontmatter.unknown_key",
                number,
                number,
                f"frontmatter key '{key}' is not allowed; the contract has two keys: type, status. Reason: {reason}",
            )
        )
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
        failures.append(
            Finding(
                "frontmatter.scalar",
                number,
                number,
                "use an unquoted, nonempty scalar from the configured closed set",
            )
        )
    values[key] = (value, number)


def _runtime_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts[-1] == "SKILL.md" or (
        len(parts) >= 3
        and parts[0] in {".agents", ".claude", ".codex"}
        and parts[1] in {"agents", "commands"}
    )


def validate_document(
    config: ProjectConfig, path: str, text: str, *, is_move: bool = False
) -> Declaration:
    """Resolve fixed name, declaration, then paths; never guess an ambiguous type."""
    fixed = config.fixed_types.get(
        path, config.fixed_types.get(PurePosixPath(path).name)
    )
    if _runtime_file(path) and fixed is None:
        return Declaration(None, "runtime_schema", None, 1)
    values, body_start, failures = _parse(text)
    exempt = fixed is not None or matches(path, config.generated)
    if exempt and body_start > 1:
        failures.append(
            Finding(
                "frontmatter.forbidden",
                1,
                body_start - 1,
                "fixed-name and generated files carry no document frontmatter",
            )
        )
    declared, type_line = values.get("type", ("", 1))
    candidates = [
        name
        for name, rule in config.types.items()
        if rule.paths and matches(path, rule.paths)
    ]
    kind, source = _resolve(fixed, declared, candidates)
    if path.startswith("docs/") and not exempt and not declared:
        failures.append(
            Finding(
                "frontmatter.type_missing",
                1,
                1,
                "maintained docs require a type scalar",
            )
        )
    failures.extend(_type_failures(config, kind, exempt, type_line))
    placement: list[Finding] = []
    if (
        kind in config.types
        and not exempt
        and not matches(path, config.types[kind].paths)
    ):
        placement.append(
            Finding(
                "placement.type_path",
                type_line,
                type_line,
                f"'{kind}' is not allowed at this path; use its configured home or request an owner placement decision",
            )
        )
    status = values.get("status", (None, 1))[0]
    failures.extend(_status_failures(config, kind, status, values, text, body_start))
    if not is_move:
        failures.extend(placement)
    return Declaration(
        kind,
        source,
        status,
        body_start,
        tuple(failures),
        tuple(placement) if is_move else (),
    )


def _resolve(
    fixed: str | None, declared: str, candidates: list[str]
) -> tuple[str | None, str | None]:
    if fixed:
        return fixed, "fixed_name"
    if declared:
        return declared, "frontmatter"
    return (candidates[0], "paths") if len(candidates) == 1 else (None, None)


def _type_failures(
    config: ProjectConfig,
    kind: str | None,
    exempt: bool,
    line: int,
) -> list[Finding]:
    if exempt or kind is None:
        return []
    if kind not in config.types:
        return [
            Finding(
                "frontmatter.type_unknown" if kind else "type.unresolved",
                line,
                line,
                "type must resolve to exactly one configured kind",
            )
        ]
    return []


def _status_failures(
    config: ProjectConfig,
    kind: str | None,
    status: str | None,
    values: dict[str, tuple[str, int]],
    text: str,
    body_start: int,
) -> list[Finding]:
    allowed = config.types[kind].status if kind in config.types else ()
    line = values.get("status", ("", 1))[1]
    failures = []
    if allowed and status is None:
        failures.append(
            Finding(
                "frontmatter.status_missing",
                1,
                1,
                "this lifecycle type requires status",
            )
        )
    if status is not None and status not in allowed:
        failures.append(
            Finding(
                "frontmatter.status_invalid",
                line,
                line,
                "status is outside the configured lifecycle set"
                if allowed
                else "this type forbids status; it has no lifecycle",
            )
        )
    if status is not None:
        for number, body_line in prose_lines(text, body_start):
            if INLINE_STATUS.match(body_line):
                failures.append(
                    Finding(
                        "frontmatter.inline_status",
                        number,
                        number,
                        "frontmatter owns status; remove the duplicate inline label",
                    )
                )
    return failures
