"""Produce deterministic, located facts; a lint-clean document still needs review."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from docreview import __version__
from docreview.frontmatter import Declaration, Finding, validate_document
from docreview.link_graph import LinkGraph, build_graph, redirect_failures
from docreview.markdown import Section, prose_lines, sections
from docreview.paths import maintained_paths, matches, repository_path

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig
    from docreview.documentation_decisions import Json

EXECUTION_PATTERNS = (
    r"\b\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d",
    r"\bexit(?:[_ ]code)?\s*[:=]\s*\d+",
    r"\breceipt\b",
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    r"```(?:console|terminal|transcript)",
)


def _term_patterns(config: ProjectConfig) -> list[tuple[str, re.Pattern[str]]]:
    terms = list(config.inline_terms)
    for source in config.term_sources:
        pattern = re.compile(source.pattern)
        content = repository_path(config.root, source.file).read_text(encoding="utf-8")
        for line in content.splitlines():
            if match := pattern.search(line):
                terms.extend(match[1].split(source.split))
    return [
        (
            term.strip(),
            re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.IGNORECASE),
        )
        for term in dict.fromkeys(terms)
        if term.strip()
    ]


def _banned_terms(
    config: ProjectConfig,
    path: str,
    text: str,
    start: int,
    terms: list[tuple[str, re.Pattern[str]]],
) -> tuple[list[Json], list[Finding]]:
    findings = []
    facts: list[Json] = []
    definitions = [
        re.compile(source.pattern)
        for source in config.term_sources
        if source.file == path
    ]
    for number, line in prose_lines(text, start):
        if line.lstrip().startswith(">") or any(
            pattern.search(line) for pattern in definitions
        ):
            continue
        unquoted = re.sub(
            r""""[^"]*"|\u201c[^\u201d]*\u201d|'[^']*'|\u2018[^\u2019]*\u2019""",
            "",
            line,
        )
        for term, pattern in terms:
            if pattern.search(unquoted):
                facts.append({"term": term, "line_start": number, "line_end": number})
                findings.append(
                    Finding(
                        "terms.banned",
                        number,
                        number,
                        f"use the canonical term instead of '{term}'",
                    )
                )
    return facts, findings


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
    terms: list[tuple[str, re.Pattern[str]]],
    is_new: bool,
) -> dict[str, Json]:
    outline = sections(text, declaration.body_start)
    banned, term_failures = _banned_terms(
        config, path, text, declaration.body_start, terms
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
        "links_resolve": not graph.failures[path],
        "inbound_links": len(graph.incoming[path]),
        "inbound_sources": incoming_sources,
        "placement_ok": not any(
            item.rule == "placement.type_path"
            for item in (*failures, *declaration.review_items)
        ),
        "banned_terms": banned,
        "execution_log_signals": signals,
        "readability": readability,
        "duplicate_candidates": [],
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
            "duplication: retrieval index is not implemented in this slice",
            "docstrings: Markdown-only input",
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
    maintained = maintained_paths(config)
    selected = maintained if paths is None else sorted(set(paths))
    if set(selected) - set(maintained):
        raise ValueError("selected paths must be existing maintained Markdown")
    if (set(new_paths or ()) | set(moved_paths or ())) - set(selected):
        raise ValueError("new/moved paths must be included in the selected files")
    corpus = {
        path: repository_path(config.root, path).read_text(encoding="utf-8")
        for path in maintained
    }
    declarations = {
        path: validate_document(config, path, text, is_move=path in (moved_paths or ()))
        for path, text in corpus.items()
    }
    graph = build_graph(
        config.root,
        corpus,
        {path: declaration.body_start for path, declaration in declarations.items()},
    )
    terms = _term_patterns(config)
    documents: list[Json] = [
        _document_report(
            config,
            path,
            corpus[path],
            declarations[path],
            graph,
            terms,
            path in (new_paths or ()),
        )
        for path in selected
    ]
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
