"""Produce deterministic, located facts; a lint-clean document still needs review."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from docreview import __version__
from docreview.duplication import load_index
from docreview.frontmatter import Declaration, Finding, validate_document
from docreview.link_graph import LinkGraph, build_graph, redirect_failures
from docreview.markdown import Section, prose_lines, sections
from docreview.paths import maintained_paths, matches, repository_path

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig, TermSource
    from docreview.documentation_decisions import Json
    from docreview.duplication import DuplicationIndex

EXECUTION_PATTERNS = (
    r"\b\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d",
    r"\bexit(?:[_ ]code)?\s*[:=]\s*\d+",
    r"\breceipt\b",
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    r"```(?:console|terminal|transcript)",
)


@dataclass(frozen=True)
class TermRule:
    """One configured term; only retired words are hard failures."""

    term: str
    pattern: re.Pattern[str]
    concept: str | None
    retired: bool


@dataclass(frozen=True)
class TermCatalog:
    """Terms plus the defining lines that must not count as occurrences."""

    rules: tuple[TermRule, ...]
    defining_lines: dict[str, frozenset[int]]


BLOCK_START = re.compile(r"\s*(?:#|[-*+] |\d+\. |>|\||_[A-Za-z]|\*\*)")


def _entry_text(
    lines: list[str], index: int, first: str, stop: re.Pattern[str] | None
) -> tuple[str, list[int]]:
    """Join a wrapped list entry until a blank line or the next block."""
    parts = [first]
    used = [index + 1]
    for offset in range(index + 1, len(lines)):
        line = lines[offset]
        if not line.strip() or BLOCK_START.match(line) or (stop and stop.search(line)):
            break
        parts.append(line.strip())
        used.append(offset + 1)
    return " ".join(parts), used


def _source_terms(
    content: str, source: TermSource
) -> tuple[list[tuple[str, str | None]], set[int]]:
    pattern = re.compile(source.pattern)
    concept_pattern = (
        re.compile(source.concept_pattern) if source.concept_pattern else None
    )
    lines = content.splitlines()
    concept: str | None = None
    found: list[tuple[str, str | None]] = []
    defining: set[int] = set()
    for index, line in enumerate(lines):
        if concept_pattern and (heading := concept_pattern.search(line)):
            concept = heading[1].strip()
            defining.add(index + 1)
        if match := pattern.search(line):
            text, used = _entry_text(lines, index, match[1], pattern)
            defining.update(used)
            for term in text.split(source.split):
                cleaned = term.strip().rstrip(".;")
                if cleaned:
                    found.append(
                        (cleaned, concept if source.scope == "concept" else None)
                    )
    return found, defining


def _compile(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


def _term_catalog(config: ProjectConfig) -> TermCatalog:
    rules: dict[tuple[str, str | None, bool], TermRule] = {}
    for term in config.inline_terms:
        if term.strip():
            rules[(term.strip(), None, True)] = TermRule(
                term.strip(), _compile(term.strip()), None, True
            )
    defining: dict[str, set[int]] = {}
    for source in config.term_sources:
        content = repository_path(config.root, source.file).read_text(encoding="utf-8")
        found, used = _source_terms(content, source)
        defining.setdefault(source.file, set()).update(used)
        retired = source.scope == "global"
        for term, concept in found:
            rules.setdefault(
                (term, concept, retired),
                TermRule(term, _compile(term), concept, retired),
            )
    return TermCatalog(
        tuple(rules.values()),
        {path: frozenset(lines) for path, lines in defining.items()},
    )


def _term_occurrences(
    path: str, text: str, start: int, catalog: TermCatalog
) -> tuple[list[Json], list[Json], list[Finding]]:
    """Quoted text, block quotes and defining entries are not occurrences."""
    banned: list[Json] = []
    usage: list[Json] = []
    findings: list[Finding] = []
    skip = catalog.defining_lines.get(path, frozenset())
    for number, line in prose_lines(text, start):
        if number in skip or line.lstrip().startswith(">"):
            continue
        unquoted = re.sub(
            r""""[^"]*"|\u201c[^\u201d]*\u201d|'[^']*'|\u2018[^\u2019]*\u2019""",
            "",
            line,
        )
        for rule in catalog.rules:
            if not rule.pattern.search(unquoted):
                continue
            if rule.retired:
                banned.append(
                    {"term": rule.term, "line_start": number, "line_end": number}
                )
                findings.append(
                    Finding(
                        "terms.banned",
                        number,
                        number,
                        f"'{rule.term}' is retired in this project; use the canonical term",
                    )
                )
            else:
                usage.append(
                    {
                        "term": rule.term,
                        "concept": rule.concept,
                        "line_start": number,
                        "line_end": number,
                    }
                )
    return banned, usage, findings


def _section_signals(
    text: str, outline: list[Section]
) -> tuple[list[Json], list[Json]]:
    signals: list[Json] = []
    readability: list[Json] = []
    lines = text.splitlines()
    for section in outline:
        body = "\n".join(lines[section.line_start - 1 : section.line_end])
        found = [
            pattern
            for pattern in EXECUTION_PATTERNS
            if re.search(pattern, body, re.IGNORECASE)
        ]
        location: dict[str, Json] = {
            "heading": section.heading,
            "line_start": section.line_start,
            "line_end": section.line_end,
        }
        if found:
            signals.append({**location, "signals": list(found)})
        words = re.findall(r"\b\w+\b", body)
        sentences = [part for part in re.split(r"[.!?]+", body) if part.strip()]
        readability.append(
            {
                **location,
                "words_per_sentence": len(words) / max(len(sentences), 1),
                "long_word_ratio": sum(len(word) > 10 for word in words)
                / max(len(words), 1),
            }
        )
    return signals, readability


def _navigation(
    config: ProjectConfig,
    path: str,
    declaration: Declaration,
    graph: LinkGraph,
    outline: list[Section],
    is_new: bool,
) -> list[Finding]:
    if not is_new or sum(section.words for section in outline) < 100:
        return []
    incoming = graph.incoming[path]
    curated = [source for source in incoming if matches(source, config.curated)]
    parents = [
        source
        for source in incoming
        if source.endswith(("/README.md", "/CONTEXT-MAP.md"))
        or source in {"README.md", "CONTEXT-MAP.md", "AGENTS.md"}
    ]
    acceptable = (
        curated
        if declaration.document_type in config.curated_required_for
        else parents + curated
    )
    if acceptable:
        return []
    return [
        Finding(
            "navigation.orphan",
            1,
            1,
            "a substantial new page needs an inbound link from its curated route"
            if declaration.document_type in config.curated_required_for
            else "a substantial new page needs an inbound link from its index or parent",
        )
    ]


def _document_report(
    config: ProjectConfig,
    path: str,
    text: str,
    declaration: Declaration,
    graph: LinkGraph,
    terms: TermCatalog,
    is_new: bool,
    duplicate_candidates: list[Json],
) -> dict[str, Json]:
    outline = sections(text, declaration.body_start)
    banned, term_usage, term_failures = _term_occurrences(
        path, text, declaration.body_start, terms
    )
    signals, readability = _section_signals(text, outline)
    failures = [
        *declaration.failures,
        *graph.failures[path],
        *term_failures,
        *redirect_failures(path, graph, config.limits.redirect_chain),
        *_navigation(config, path, declaration, graph, outline, is_new),
    ]
    located: list[Json] = [
        finding.as_json() for finding in (*failures, *declaration.review_items)
    ]
    route = "revise" if failures else "review"
    incoming_sources: list[Json] = []
    incoming_sources.extend(sorted(graph.incoming[path]))
    facts: dict[str, Json] = {
        "bytes": len(text.encode("utf-8")),
        "words": len(re.findall(r"\b\w+\b", text)),
        "sections": len(outline),
        "heading_tree": [section.as_json() for section in outline],
        "links_total": graph.totals[path],
        "links_resolve": None if graph.unassessed[path] else not graph.failures[path],
        "inbound_links": len(graph.incoming[path]),
        "inbound_sources": incoming_sources,
        "placement_ok": None
        if declaration.document_type is None
        else not any(
            item.rule == "placement.type_path"
            for item in (*failures, *declaration.review_items)
        ),
        "banned_terms": banned,
        "term_usage": term_usage,
        "execution_log_signals": signals,
        "readability": readability,
        "duplicate_candidates": duplicate_candidates,
        "docstring_facts": [],
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    return {
        "path": path,
        "type": declaration.document_type,
        "type_source": declaration.type_source,
        "status": declaration.status,
        "facts": facts,
        "hard_failures": [item.as_json() for item in failures],
        "requests": [],
        "criteria": {},
        "route": route,
        "reasons": ["deterministic_requirement_failure"]
        if failures
        else ["semantic_review_not_assessed"],
        "handoff": {
            "target": "authoring_agent" if failures else "documentation_review_agent",
            "failed": [item.rule for item in failures],
            "uncertain": [item.rule for item in declaration.review_items],
            "located_items": located,
            "instructions": "Correct the located rule violations within project authority."
            if failures
            else "Review document purpose, audience, ownership and content; deterministic facts alone do not establish quality.",
        },
        "not_assessed": [
            "model criteria: facts-only run",
            "duplication: candidate ownership and content not assessed"
            if duplicate_candidates
            else f"duplication: no candidate pairs at overlap >= {config.limits.duplicate_overlap}; ownership and content not assessed",
            "duplication: lexical retrieval cannot establish absence of paraphrased duplication",
            "docstrings: Markdown-only input",
            *[
                f"links: {finding.message} at line {finding.line_start}"
                for finding in graph.unassessed[path]
            ],
        ],
    }


def inspect_repository(
    config: ProjectConfig,
    paths: list[str] | None = None,
    *,
    new_paths: set[str] | None = None,
    moved_paths: set[str] | None = None,
) -> dict[str, Json]:
    """Inspect selected maintained pages against the whole maintained link corpus."""
    return inspect_corpus(
        config, read_corpus(config), paths, new_paths=new_paths, moved_paths=moved_paths
    )


def read_corpus(config: ProjectConfig) -> dict[str, str]:
    """Capture source text once so facts and questions use the same snapshot."""
    return {
        path: repository_path(config.root, path).read_text(encoding="utf-8")
        for path in maintained_paths(config)
    }


def inspect_corpus(
    config: ProjectConfig,
    corpus: dict[str, str],
    paths: list[str] | None = None,
    *,
    new_paths: set[str] | None = None,
    moved_paths: set[str] | None = None,
    allow_filesystem: bool = True,
) -> dict[str, Json]:
    """Use an explicit corpus for repository or before/after snapshot review."""
    selected = sorted(corpus) if paths is None else sorted(set(paths))
    if set(selected) - corpus.keys():
        raise ValueError("selected paths must belong to the supplied corpus")
    if (set(new_paths or ()) | set(moved_paths or ())) - set(selected):
        raise ValueError("new/moved paths must be included in the selected files")
    declarations = {
        path: validate_document(config, path, text, is_move=path in (moved_paths or ()))
        for path, text in corpus.items()
    }
    graph = build_graph(
        config.root,
        corpus,
        {path: declaration.body_start for path, declaration in declarations.items()},
        allow_filesystem=allow_filesystem,
    )
    index = load_index(
        config,
        corpus,
        {path: declaration.body_start for path, declaration in declarations.items()},
    )
    duplicate_candidates = _duplicate_candidates(
        index, set(selected), config.limits.duplicate_overlap
    )
    terms = _term_catalog(config)
    documents: list[Json] = [
        _document_report(
            config,
            path,
            corpus[path],
            declarations[path],
            graph,
            terms,
            path in (new_paths or ()),
            duplicate_candidates[path],
        )
        for path in selected
    ]
    for value in documents:
        if isinstance(value, dict):
            facts = value["facts"]
            if isinstance(facts, dict):
                facts["index_entries"] = _routing_entries(
                    str(value["path"]), graph, corpus
                )
    # Summary uses the final findings, including terms and navigation, not only declarations.
    hard_failures = sum(
        len(row["hard_failures"])
        for row in documents
        if isinstance(row, dict) and isinstance(row["hard_failures"], list)
    )
    return {
        "policy_version": "docreview-1",
        "engine_version": __version__,
        "config_sha256": config.sha256,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": {"requested": config.model, "answered": None},
        "thresholds": dict(config.thresholds),
        "documents": documents,
        "duplication_index": index.metadata(),
        "summary": {
            "documents": len(documents),
            "pass": 0,
            "revise": sum(
                isinstance(row, dict) and row["route"] == "revise" for row in documents
            ),
            "review": sum(
                isinstance(row, dict) and row["route"] == "review" for row in documents
            ),
            "hard_failures": hard_failures,
            "model_failures": 0,
        },
        "warnings": list(config.warnings),
    }


def _duplicate_candidates(
    index: DuplicationIndex, selected: set[str], threshold: float
) -> dict[str, list[Json]]:
    result: dict[str, list[Json]] = {path: [] for path in selected}
    for pair in index.candidates(selected, threshold):
        for path in {pair.left.path, pair.right.path} & selected:
            result[path].append(pair.as_json())
    return result


def report_exit_code(config: ProjectConfig, report: dict[str, Json]) -> int:
    """Contributed reports stay advisory; incomplete runs are handled separately."""
    summary = report["summary"]
    if (
        isinstance(summary, dict)
        and config.authority == "owned"
        and summary.get("hard_failures", 0)
    ):
        return 1
    return 0


def _routing_entries(path: str, graph: LinkGraph, corpus: dict[str, str]) -> list[Json]:
    from docreview.link_graph import local_target
    from docreview.markdown import links

    entries: list[Json] = []
    for source in sorted(graph.incoming[path]):
        lines = corpus[source].splitlines()
        for link in links(corpus[source]):
            try:
                target = local_target(source, link.target)
            except ValueError:
                continue
            if target is not None and target[0] == path:
                entries.append(
                    {
                        "path": source,
                        "line_start": link.line,
                        "text": lines[link.line - 1],
                    }
                )
    return entries
