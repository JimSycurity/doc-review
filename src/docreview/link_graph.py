"""Resolve local links and index incoming references without making network calls."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from docreview.frontmatter import Finding
from docreview.markdown import anchors, links, redirect
from docreview.paths import repository_path

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class LinkGraph:
    """Graph records refer only to the selected maintained Markdown corpus."""

    incoming: dict[str, set[str]]
    failures: dict[str, list[Finding]]
    totals: dict[str, int]
    redirects: dict[str, str]


def local_target(source: str, target: str) -> tuple[str, str] | None:
    """Return a normalized repository path and fragment, or ignore an external URL."""
    if target.startswith("missing-reference:"):
        raise ValueError("reference link has no definition")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    raw = unquote(parsed.path)
    path = (
        posixpath.normpath(posixpath.join(posixpath.dirname(source), raw))
        if raw
        else source
    )
    if raw.startswith("/"):
        path = posixpath.normpath(raw.lstrip("/"))
    if path == ".." or path.startswith("../") or "\\" in path:
        raise ValueError("link leaves repository")
    return path, unquote(parsed.fragment)


def build_graph(
    root: Path, corpus: dict[str, str], starts: dict[str, int]
) -> LinkGraph:
    """Count direct incoming pages, ignoring self-links and duplicates from a page."""
    graph = LinkGraph(
        {path: set() for path in corpus}, {path: [] for path in corpus}, {}, {}
    )
    for path, text in corpus.items():
        found = links(text, starts[path])
        graph.totals[path] = len(found)
        for link in found:
            _add_link(graph, root, corpus, path, link.target, link.line)
        target = _redirect_target(path, text, starts[path], graph.failures[path])
        if target:
            graph.redirects[path] = target
    return graph


def _redirect_target(
    path: str, text: str, start: int, failures: list[Finding]
) -> str | None:
    stub = redirect(text, start)
    if not stub or failures:
        return None
    target = local_target(path, stub.target)
    if target is None or (target[0] == path and target[1]):
        return None
    return target[0]


def _add_link(
    graph: LinkGraph,
    root: Path,
    corpus: dict[str, str],
    source: str,
    raw: str,
    line: int,
) -> None:
    try:
        target = local_target(source, raw)
        if target is None:
            return
        path, fragment = target
        file = repository_path(root, path)
        if not file.exists():
            raise ValueError("relative link target does not exist")
        if fragment and file.suffix == ".md":
            text = corpus[path] if path in corpus else file.read_text(encoding="utf-8")
            if fragment not in anchors(text):
                raise ValueError("Markdown heading anchor does not exist")
        if source != path and path in graph.incoming:
            graph.incoming[path].add(source)
    except ValueError as exc:
        graph.failures[source].append(Finding("links.unresolved", line, line, str(exc)))


def redirect_failures(path: str, graph: LinkGraph, limit: int) -> list[Finding]:
    """Cycles fail even before the length budget is exceeded."""
    seen = set()
    current = path
    while current in graph.redirects:
        if current in seen:
            return [
                Finding(
                    "navigation.redirect_cycle", 1, 1, "redirect chain contains a cycle"
                )
            ]
        seen.add(current)
        current = graph.redirects[current]
        if len(seen) > limit:
            return [
                Finding(
                    "navigation.redirect_chain",
                    1,
                    1,
                    f"redirect chain exceeds {limit} links",
                )
            ]
    return []
