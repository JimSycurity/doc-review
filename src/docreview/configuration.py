"""Load the closed project declaration using only standard-library parsers."""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

from docreview import __version__
from docreview.documentation_decisions import Json, as_object

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

FIXED_TYPES = {
    "README.md": "readme",
    "AGENTS.md": "agent_instructions",
    "CLAUDE.md": "agent_instructions",
    "CONTEXT.md": "context",
    "CONTEXT-MAP.md": "index",
}
DEFAULT_SCOPE = (*FIXED_TYPES, "docs/**/*.md", ".agents/**/*.md", ".claude/**/*.md")
DEFAULT_EXCLUDED = (".archive/**", ".tmp/**", "**/fixtures/**", "docs/archive/**")
DEFAULT_PATHS = {
    "context": ("docs/context/**",),
    "runbook": ("docs/usage/**",),
    "reference": ("docs/reference/**",),
    "design": ("docs/design/**",),
    "plan": ("docs/plans/**",),
    "adr": ("docs/adr/**", "docs/decisions/**"),
    "policy": ("docs/policies/**",),
    "analysis": ("docs/analysis/**",),
    "report": ("docs/reports/**",),
    "index": ("docs/indexes/**",),
}
STATUSES = {
    "design": ("draft", "proposed", "accepted", "superseded", "rejected"),
    "plan": ("draft", "active", "completed", "superseded"),
    "adr": ("proposed", "accepted", "superseded", "rejected"),
}
TYPE_NAMES = (
    *FIXED_TYPES.values(),
    "runbook",
    "reference",
    "design",
    "plan",
    "adr",
    "policy",
    "analysis",
    "report",
)


@dataclass(frozen=True)
class TypeRule:
    """Project-owned placement, lifecycle and audience for one declared kind."""

    paths: tuple[str, ...] = ()
    status: tuple[str, ...] = ()
    purpose: str = ""
    audience: str = ""
    rubric: str = "common"
    operator_facing: bool = False


@dataclass(frozen=True)
class TermSource:
    """Select terms from one explicitly named repository file.

    A ``concept`` source lists synonyms to avoid for one glossary entry, so a
    match is an advisory usage fact for the model to judge in context. Only a
    ``global`` source, or the inline list, names words retired everywhere.
    """

    file: str
    pattern: str
    split: str
    scope: str = "concept"
    concept_pattern: str = ""


@dataclass(frozen=True)
class Limits:
    """Bound expensive review work; limits never authorize truncating text."""

    request_bytes: int = 32_000
    redirect_chain: int = 2
    duplicate_overlap: float = 0.5


@dataclass(frozen=True)
class Enforcement:
    """Record who promoted a model criterion; facts have their own gate."""

    level: str = "advisory"
    issue: int = 0


@dataclass(frozen=True)
class ProjectConfig:
    """Validated contract between a project and this version of the engine."""

    root: Path
    sha256: str
    authority: str
    standard: str
    types: Mapping[str, TypeRule]
    maintained: tuple[str, ...] = DEFAULT_SCOPE
    excluded: tuple[str, ...] = DEFAULT_EXCLUDED
    generated: tuple[str, ...] = ("docs/**/README.md",)
    fixed_types: Mapping[str, str] = field(default_factory=lambda: dict(FIXED_TYPES))
    curated: tuple[str, ...] = ("CONTEXT-MAP.md", "AGENTS.md", "docs/history/README.md")
    curated_required_for: tuple[str, ...] = ("agent_instructions", "runbook", "context")
    inline_terms: tuple[str, ...] = ()
    term_sources: tuple[TermSource, ...] = ()
    limits: Limits = Limits()
    model: str = "typesafe/jev-1.14"
    thresholds: Mapping[str, float] = field(default_factory=dict)
    enforcement: Mapping[str, Enforcement] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def table(value: Json, keys: set[str], name: str) -> dict[str, Json]:
    """Reject unknown keys instead of silently accepting misspelled policy."""
    result = as_object(value, name)
    if unknown := result.keys() - keys:
        raise ValueError(f"{name}: unknown keys: {', '.join(sorted(unknown))}")
    return result


def text(row: Mapping[str, Json], key: str, default: str = "") -> str:
    """Read a scalar without coercing boolean or numeric values."""
    value = row.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def strings(
    row: Mapping[str, Json], key: str, default: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Require nonempty string list entries; an empty list can disable a rule."""
    value = row.get(key, list(default))
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a list of nonempty strings")
    return tuple(cast("list[str]", value))


def integer(row: Mapping[str, Json], key: str, default: int) -> int:
    value = row.get(key, default)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a nonnegative integer")
    return value


def fraction(row: Mapping[str, Json], key: str, default: float) -> float:
    value = row.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{key} must be a finite fraction")
    return float(value)


def relative_path(value: str) -> str:
    """Keep all reads inside the selected repository, including symlinks later."""
    if (
        not value
        or value.startswith(("/", "!"))
        or ".." in value.split("/")
        or "\\" in value
    ):
        raise ValueError("paths must be relative to the repository without '..'")
    return value


def patterns(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        relative_path(value.removeprefix("!").removeprefix("/"))
    return values


def _type(name: str, value: Json, default: TypeRule) -> TypeRule:
    row = table(
        value,
        {"paths", "status", "purpose", "audience", "rubric", "operator_facing"},
        f"types.{name}",
    )
    rubric = text(row, "rubric", default.rubric)
    if rubric not in {*TYPE_NAMES, "common", "human_instructions"}:
        raise ValueError(f"types.{name}: unknown rubric")
    operator_facing = row.get("operator_facing", default.operator_facing)
    if not isinstance(operator_facing, bool):
        raise ValueError("operator_facing must be boolean")
    return TypeRule(
        patterns(strings(row, "paths", default.paths)),
        strings(row, "status", default.status),
        text(row, "purpose", default.purpose),
        text(row, "audience", default.audience),
        rubric,
        operator_facing,
    )


def _review(row: dict[str, Json]) -> tuple[str, str]:
    standard = text(row, "standard", "skillz-default")
    authority = text(row, "authority", "contributed")
    if standard not in {"skillz-default", "project"} or authority not in {
        "owned",
        "contributed",
    }:
        raise ValueError("unknown standard or authority")
    if "standard_version" in row and integer(row, "standard_version", 1) != 1:
        raise ValueError("unsupported standard_version")
    if text(row, "engine", f"docreview=={__version__}") != f"docreview=={__version__}":
        raise ValueError(
            f"engine must pin the running version: docreview=={__version__}"
        )
    return standard, authority


def _terms(row: dict[str, Json]) -> tuple[TermSource, ...]:
    values = row.get("sources", [])
    if not isinstance(values, list):
        raise ValueError("terms.sources must be a list")
    sources = []
    for value in values:
        entry = table(
            value,
            {"file", "pattern", "split", "scope", "concept_pattern"},
            "terms.sources",
        )
        pattern = text(entry, "pattern")
        if _capture_count(pattern) != 1 or not text(entry, "split", ", "):
            raise ValueError("term pattern needs one capture and a nonempty split")
        scope = text(entry, "scope", "concept")
        if scope not in {"concept", "global"}:
            raise ValueError("terms.sources scope must be concept or global")
        concept_pattern = text(entry, "concept_pattern")
        if concept_pattern and _capture_count(concept_pattern) != 1:
            raise ValueError("concept_pattern needs exactly one capture")
        if concept_pattern and scope == "global":
            raise ValueError("concept_pattern applies only to concept-scoped sources")
        sources.append(
            TermSource(
                relative_path(text(entry, "file")),
                pattern,
                text(entry, "split", ", "),
                scope,
                concept_pattern,
            )
        )
    return tuple(sources)


def _thresholds(value: Json) -> dict[str, float]:
    defaults = {
        "accept_above": 0.9,
        "reject_below": 0.1,
        "min_confidence": 0.8,
        "type_resolution": 0.85,
        "duplication_same": 0.8,
    }
    row = table(value, set(defaults), "thresholds")
    result = {key: fraction(row, key, default) for key, default in defaults.items()}
    if not result["reject_below"] < 0.5 < result["accept_above"]:
        raise ValueError("thresholds must put reject below and accept above .5")
    return result


def _enforcement(value: Json, authority: str) -> dict[str, Enforcement]:
    result = {}
    for key, raw in as_object(value, "enforcement").items():
        row = table(raw, {"level", "issue"}, f"enforcement.{key}")
        level = text(row, "level", "advisory")
        issue = integer(row, "issue", 0)
        if level not in {"advisory", "enforcing"} or (
            level == "enforcing" and issue == 0
        ):
            raise ValueError(
                "enforcement requires advisory/enforcing and an Issue for promotion"
            )
        result[key] = Enforcement(
            "advisory" if authority == "contributed" else level, issue
        )
    return result


def load_config(root: Path) -> ProjectConfig:
    """Validate the declaration, or use read-only defaults when none exists."""
    root = root.resolve(strict=True)
    path = root / "docs-review.toml"
    if not path.resolve().is_relative_to(root):
        raise ValueError("declaration must remain inside the repository")
    payload = path.read_bytes() if path.exists() else b""
    raw = cast("dict[str, Json]", tomllib.loads(payload.decode("utf-8")))
    sections = {
        "review",
        "scope",
        "types",
        "indexes",
        "terms",
        "limits",
        "model",
        "thresholds",
        "enforcement",
    }
    table(raw, sections, "declaration")
    return _with_warnings(_build_config(root, payload, raw))


def default_config(root: Path) -> ProjectConfig:
    """Use read-only defaults for explicit bundles without discovering project files."""
    return _build_config(root.resolve(), b"", {})


def _build_config(root: Path, payload: bytes, raw: dict[str, Json]) -> ProjectConfig:
    review = table(
        raw.get("review", {}),
        {"standard", "standard_version", "authority", "engine"},
        "review",
    )
    standard, authority = _review(review)
    scope = table(
        raw.get("scope", {}),
        {"maintained", "excluded", "generated", "fixed_types"},
        "scope",
    )
    types = {
        name: TypeRule(
            DEFAULT_PATHS.get(name, ()),
            STATUSES.get(name, ()),
            rubric="human_instructions" if name == "runbook" else name,
        )
        for name in TYPE_NAMES
    }
    for name, value in as_object(raw.get("types", {}), "types").items():
        types[name] = _type(name, value, types.get(name, TypeRule()))
    default_fixed: dict[str, Json] = dict(FIXED_TYPES)
    fixed = as_object(
        scope.get("fixed_types", default_fixed),
        "fixed_types",
    )
    fixed_types = {relative_path(key): text(fixed, key) for key in fixed}
    if set(fixed_types.values()) - types.keys():
        raise ValueError("fixed_types names an unknown type")
    return _finish_config(
        root, payload, raw, standard, authority, scope, types, fixed_types
    )


def _finish_config(
    root: Path,
    payload: bytes,
    raw: dict[str, Json],
    standard: str,
    authority: str,
    scope: dict[str, Json],
    types: dict[str, TypeRule],
    fixed_types: dict[str, str],
) -> ProjectConfig:
    indexes = table(
        raw.get("indexes", {}), {"curated", "curated_required_for"}, "indexes"
    )
    terms = table(raw.get("terms", {}), {"sources", "inline"}, "terms")
    limits = table(
        raw.get("limits", {}),
        {"request_bytes", "redirect_chain", "duplicate_overlap"},
        "limits",
    )
    request_bytes = integer(limits, "request_bytes", 32_000)
    if not 0 < request_bytes <= 32_000:
        raise ValueError("request_bytes must be from 1 through 32000")
    model = table(raw.get("model", {}), {"provider", "id"}, "model")
    if (
        text(model, "provider", "openrouter") != "openrouter"
        or not text(model, "id", "typesafe/jev-1.14").strip()
    ):
        raise ValueError("model requires openrouter and a nonempty pinned id")
    required = strings(
        indexes, "curated_required_for", ("agent_instructions", "runbook", "context")
    )
    if set(required) - types.keys():
        raise ValueError("curated_required_for names an unknown type")
    return ProjectConfig(
        root=root,
        sha256=hashlib.sha256(payload).hexdigest(),
        authority=authority,
        standard=standard,
        types=types,
        maintained=patterns(strings(scope, "maintained", DEFAULT_SCOPE)),
        excluded=patterns(strings(scope, "excluded", DEFAULT_EXCLUDED)),
        generated=patterns(strings(scope, "generated", ("docs/**/README.md",))),
        fixed_types=fixed_types,
        curated=patterns(
            strings(
                indexes,
                "curated",
                ("CONTEXT-MAP.md", "AGENTS.md", "docs/history/README.md"),
            )
        ),
        curated_required_for=required,
        inline_terms=strings(terms, "inline"),
        term_sources=_terms(terms),
        limits=Limits(
            request_bytes,
            integer(limits, "redirect_chain", 2),
            fraction(limits, "duplicate_overlap", 0.5),
        ),
        model=text(model, "id", "typesafe/jev-1.14"),
        thresholds=_thresholds(raw.get("thresholds", {})),
        enforcement=_enforcement(raw.get("enforcement", {}), authority),
    )


def _capture_count(pattern: str) -> int:
    try:
        return re.compile(pattern).groups
    except re.error:
        raise ValueError("term pattern is not a valid regular expression") from None


def _with_warnings(config: ProjectConfig) -> ProjectConfig:
    from docreview.paths import maintained_paths, matches

    paths = maintained_paths(config)
    warnings = tuple(
        f"types.{name}: paths glob matches no maintained file: {pattern}"
        for name, rule in config.types.items()
        for pattern in rule.paths
        if not any(matches(path, (pattern,)) for path in paths)
    )
    return replace(config, warnings=warnings)
